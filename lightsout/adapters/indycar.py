"""IndyCar: the public JSON feeds behind leaderboard.indycar.com.

The schedule feed lists every on-track broadcast window per event (times in UTC),
which doubles as the session list.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import RACE, AdapterResult, Entry, Event, ResultRow, Session, StandingRow, Standings, kind_from_name
from .base import get_json, get_text, html_tables, slugify

FEED = "https://indycar.blob.core.windows.net/racecontrol"
PREFIX = "NTT INDYCAR SERIES - "


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def parse_schedule(data: dict) -> list[Event]:
    events = []
    for race in data["schedule"]["race"]:
        sessions = []
        tba = race.get("green_flag_tbd_time") == "1"
        broadcasts = (race.get("broadcasts") or {}).get("broadcast") or []
        if isinstance(broadcasts, dict):
            broadcasts = [broadcasts]  # the feed comes from XML: a lone broadcast isn't wrapped in a list
        for b in broadcasts:
            if not b["name"].startswith(PREFIX):
                continue  # Indy NXT and support series share the feed
            if tba and b["start"] == b.get("end"):
                continue  # placeholder at midnight Eastern until the time is announced: green_flag below says TBA
            name = b["name"][len(PREFIX):]
            if name.lower().startswith("race"):
                name = "Race"
            sessions.append(Session(slugify(name), name, kind_from_name(name),
                                    start=_utc(b["start"]), end=_utc(b["end"]) if b.get("end") else None))
        if not any(s.kind == RACE for s in sessions) and race.get("green_flag"):
            gf = _utc(race["green_flag"])
            sessions.append(Session("race", "Race", RACE, start=None if tba else gf, day=gf.date() if tba else None))
        first = min((s.start or datetime.combine(s.day, datetime.min.time(), timezone.utc)) for s in sessions)
        events.append(Event(
            key=f"{first.year}-{race['eventid']}", season=first.year, name=race["name"],
            venue=race.get("trackname", ""), country=race.get("country", ""),
            url=race.get("link_url", ""), sessions=sessions,
        ))
    return events


def parse_drivers(data: dict) -> list[Entry]:
    return [Entry(d["name"], d.get("team") or "", d.get("number") or "")
            for d in data["drivers"]["driver"] if d.get("seriesid") == "55"]


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events = parse_schedule(await get_json(f"{FEED}/schedulefeed.json"))
    entries = parse_drivers(await get_json(f"{FEED}/driversfeed.json"))
    return AdapterResult(events, sorted({e.season for e in events}), entries)


def parse_standings(page: str, teams: dict[str, str]) -> list[StandingRow]:
    table = next((t for t in html_tables(page) if t[0][:3] == ["Rank", "No.", "Driver"]), [])
    head = table[0] if table else []
    rows = []
    for r in table[1:]:
        if len(r) != len(head) or not r[0].isdigit():
            continue
        name = r[head.index("Driver")]
        rows.append(StandingRow(int(r[0]), name, teams.get(name, ""), float(r[head.index("Points")] or 0)))
    return rows


def parse_last_race(feed: dict) -> tuple[str, list[ResultRow]] | None:
    """The live-timing feed keeps the final classification of the most recent session."""
    t = feed["timing_results"]
    if t["heartbeat"].get("SessionType") != "R":
        return None
    rows = []
    for c in sorted(t.get("Item") or [], key=lambda c: int(c.get("rank") or 999)):
        gap = c.get("diff") or ""
        time = c.get("totalTime", "") if c.get("rank") == 1 else (
            f"+{gap}" if gap and not gap.startswith("-") and "lap" not in gap.lower() else gap)
        if c.get("status") not in (None, "", "Active", "Running"):
            time = c["status"]
        rows.append(ResultRow(str(c.get("rank", "")), f"{c.get('firstName', '')} {c.get('lastName', '')}".strip(),
                              time=time))
    return t["heartbeat"].get("eventName", ""), rows


async def standings(series_id: str) -> Standings | None:
    teams = {e.name: e.team for e in parse_drivers(await get_json(f"{FEED}/driversfeed.json"))}
    st = Standings(drivers=parse_standings(await get_text("https://www.indycar.com/Standings"), teams),
                   season_label="NTT INDYCAR SERIES")
    last = parse_last_race(await get_json(f"{FEED}/timingscoring-ris.json"))
    if last:
        st.last_event, st.last_results = last
        st.last_session = "Race"
        for r in st.last_results:
            r.team = teams.get(r.name, "")
    return st
