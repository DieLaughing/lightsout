"""FIA Formula 2 and Formula 3: calendar page, then each round's page.

Each round page embeds a `meetingSessions` list (local start time + GMT offset)
and a schema.org SportsEvent with the round's name and venue.
"""
from __future__ import annotations

import base64
import html
import json
import re
import unicodedata
from datetime import datetime

from ..models import (PRACTICE, QUALIFYING, RACE, SPRINT, AdapterResult, Entry, Event, ResultRow, Session,
                      StandingRow, Standings)
from .base import find_sports_event, get_text, html_tables, is_local_midnight, json_ld, raw_json_after

SITES = {"f2": "https://www.fiaformula2.com", "f3": "https://www.fiaformula3.com"}

# Both series run a Saturday sprint (Race 1) and a Sunday feature (Race 2).
RACE_NAMES = {"Race 1": ("Sprint Race", SPRINT), "Race 2": ("Feature Race", RACE)}


def parse_round(page: str, season: int, slug: str, url: str) -> Event | None:
    raw = raw_json_after(page, '"meetingSessions":')
    if not raw:
        return None
    sessions = []
    for s in raw:
        local = datetime.fromisoformat(f"{s['startTime']}{s.get('gmtOffset') or '+00:00'}")
        end = datetime.fromisoformat(f"{s['endTime']}{s.get('gmtOffset') or '+00:00'}") if s.get("endTime") else None
        stype = s.get("sessionType", "")
        if stype == "Race":
            name, kind = RACE_NAMES.get(s["session"], (s["session"], RACE))
        elif stype == "Qualifying":
            name, kind = "Qualifying", QUALIFYING
        else:
            name, kind = "Practice", PRACTICE
        tba = s.get("sessionStatus") == "TBC" or is_local_midnight(local)
        sessions.append(Session(
            key=re.sub(r"\W+", "-", s["session"].lower()),
            name=name, kind=kind,
            start=None if tba else local, end=None if tba else end,
            day=local.date() if tba else None,
        ))
    ev = find_sports_event(json_ld(page)) or {}
    subs = ev.get("subEvent") or []
    title = subs[0]["name"].split(" - ", 1)[-1] if subs else slug.replace("-", " ").title()
    loc = ev.get("location") or {}
    return Event(key=f"{season}-{slug}", season=season, name=title,
                 venue=loc.get("name", ""), country=str(loc.get("address", "")).split(",")[-1].strip(),
                 url=url, sessions=sessions)


def parse_drivers(page: str) -> list[Entry]:
    """Driver cards link to /en/drivers/<slug> with first and last name inside. The link's analytics tag
    (data-uds-a7s-context, base64 JSON) names the team: {"driver_team": "Invicta Racing", ...}."""
    found: dict[str, Entry] = {}
    for attrs, slug, inner in re.findall(r'<a([^>]+)href="/en/drivers/([a-z0-9-]+)"[^>]*>(.*?)</a>', page, re.S):
        parts = [p.strip() for p in html.unescape(re.sub(r"<[^>]+>", "\n", inner)).split("\n") if p.strip()]
        if not parts or slug in found:
            continue
        team, ctx = "", re.search(r'data-uds-a7s-context="([^"]+)"', attrs)
        if ctx:
            try:
                team = json.loads(base64.b64decode(ctx[1])).get("driver_team") or ""
            except ValueError:  # not base64 / not JSON: no team, the name still counts
                pass
        found[slug] = Entry(" ".join(parts[:2]), team)
    return list(found.values())


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    site = SITES[series_id]
    events, covered = [], []
    for season in seasons:
        try:
            cal = await get_text(f"{site}/en/racing/{season}")
        except Exception:
            continue
        slugs = list(dict.fromkeys(re.findall(rf'/en/racing/{season}/([a-z0-9-]+)', cal)))
        season_events = []
        for slug in slugs:
            url = f"{site}/en/racing/{season}/{slug}"
            ev = parse_round(await get_text(url), season, slug, url)
            if ev:
                season_events.append(ev)
        if season_events:
            events += season_events
            covered.append(season)
    try:
        entries = parse_drivers(await get_text(f"{site}/en/drivers"))
    except Exception:
        entries = []  # the follow picker can live without it
    return AdapterResult(events, covered, entries)


def parse_standings(page: str, full_names: dict[str, str] | None = None) -> list[StandingRow]:
    """Rows start "1R. Câmara" (position glued to the short name) and end with total points."""
    tables = html_tables(page)
    rows = []
    for r in (tables[0] if tables else []):
        m = re.match(r"^(\d+)(\D.*)$", r[0]) if r else None
        if not m or not r[-1].replace(".", "").isdigit():
            continue
        short = m.group(2).strip()
        rows.append(StandingRow(int(m.group(1)), (full_names or {}).get(_short(short), short), points=float(r[-1])))
    return rows


def _short(name: str) -> str:
    """"Rafael Câmara" / "R. Camara" -> "r. camara": the key both spellings share."""
    name = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode().lower().strip()
    if re.match(r"^\w\. ", name):
        return name
    first, _, last = name.partition(" ")
    return f"{first[:1]}. {last}" if last else name


async def standings(series_id: str) -> Standings | None:
    from ..live import fia  # live timing doubles as the last-race result source

    site = SITES[series_id]
    year = datetime.now().year
    drivers = parse_drivers(await get_text(f"{site}/en/drivers"))
    names = {_short(e.name): e.name for e in drivers}
    team_of = {e.name: e.team for e in drivers}  # this season's seats
    st = Standings(season_label=f"{year}")
    for season in (year, year - 1):
        st.drivers = parse_standings(await get_text(f"{site}/en/standings/{season}/drivers"), names)
        if st.drivers:
            st.teams = parse_standings(await get_text(f"{site}/en/standings/{season}/teams"))
            st.season_label = str(season)
            if season != year:
                team_of = {}  # last season's table: today's seats would be wrong
            for r in st.drivers:
                r.team = team_of.get(r.name, "")
            break
    live = await fia.fetch_once(series_id)
    snap = live.snapshot()
    if snap["kind"] == "race" and snap["rows"]:
        st.last_event, st.last_session = snap["event"], snap["session"]
        for r in snap["rows"]:
            time = r["status"] or (f"+{r['gap'].lstrip('+')}" if r["gap"] else f"{r['laps']} laps")
            name = names.get(_short(r["name"]), r["name"])
            st.last_results.append(ResultRow(str(r["pos"]) if r["pos"] < 99 else "", name, team_of.get(name, ""),
                                             time=time, code=r["code"]))
    return st if st.drivers or st.last_results else None
