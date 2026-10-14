"""MotoGP, Moto2 and Moto3: the public JSON API behind motogp.com (api.motogp.pulselive.com).

One calendar feed covers all three classes: each Grand Prix lists its on-track sessions under "broadcasts", each tagged
with its class (MGP / MT2 / MT3), with start and end times carrying the track's UTC offset. Next season's calendar is
in the same feed as soon as it is announced.

  /events?seasonYear=<year>                     GPs (kind GP), tests (TEST) and media days (MEDIA)
  /riders                                       this season's riders, with class, team and number
  /results/seasons, /results/categories?seasonUuid=
  /results/standings?seasonUuid=&categoryUuid=  riders' championship (no team table in the API)
  /results/events?seasonUuid=&isFinished=true   finished GPs (tests are flagged test: true)
  /results/sessions?eventUuid=&categoryUuid=    sessions; type RAC (or RAC1/RAC2 when a race is split) is the GP
  /results/session/<id>/classification          position, rider, team, time, gap; status OUTSTND = did not finish
"""
from __future__ import annotations

import re
from datetime import timedelta

from ..models import PRACTICE, QUALIFYING, RACE, SPRINT, AdapterResult, Entry, Event, ResultRow, Session, StandingRow, Standings
from .base import get_json, parse_dt

API = "https://api.motogp.pulselive.com/motogp/v1"
SITE = "https://www.motogp.com/en/calendar"
CLASS = {"motogp": ("MGP", "MotoGP"), "moto2": ("MT2", "Moto2"), "moto3": ("MT3", "Moto3")}  # series -> (acronym, API name)

SESSION = {  # shortname -> (name, kind)
    "FP1": ("Free Practice 1", PRACTICE), "FP2": ("Free Practice 2", PRACTICE), "PR": ("Practice", PRACTICE),
    "WUP": ("Warm Up", PRACTICE), "Q1": ("Qualifying 1", QUALIFYING), "Q2": ("Qualifying 2", QUALIFYING),
    "SPR": ("Sprint", SPRINT), "RAC": ("Race", RACE), "RAC1": ("Race 1", RACE), "RAC2": ("Race 2", RACE),
}
LENGTH = {SPRINT: timedelta(minutes=25), RACE: timedelta(minutes=45)}
SMALL = {"of", "the", "and", "de", "di", "del"}


def gp_name(raw: str) -> str:
    """'QATAR AIRWAYS GRAND PRIX OF QATAR ' -> 'Grand Prix of Qatar': the title sponsor goes, the place stays."""
    raw = " ".join(raw.split())
    i = raw.upper().find("GRAND PRIX")
    words = (raw[i:] if i >= 0 else raw).split()
    return " ".join(w.lower() if w.lower() in SMALL and n else w.capitalize() for n, w in enumerate(words))


def parse_events(data: list, series_id: str) -> list[Event]:
    acronym = CLASS[series_id][0]
    events = []
    for e in data:
        if e.get("kind") != "GP":
            continue  # tests and media days
        sessions = []
        for b in e.get("broadcasts") or []:
            if b.get("type") != "SESSION" or (b.get("category") or {}).get("acronym") != acronym:
                continue
            name, kind = SESSION.get(b["shortname"], (b.get("name") or b["shortname"], PRACTICE))
            start = parse_dt(b["date_start"])
            end = parse_dt(b["date_end"]) if b.get("date_end") else None
            if end is None or end <= start:
                end = start + LENGTH.get(kind, timedelta(minutes=45))  # races are listed with end = start
            sessions.append(Session(key=f"{start.date().isoformat()}-{b['shortname'].lower()}", name=name, kind=kind,
                                    start=start, end=end))
        start = parse_dt(e["date_start"])
        if not sessions:  # next season: dates are out, the timetable isn't
            sessions = [Session(key="weekend", name="Race weekend", kind=RACE, day=start.date(),
                                end_day=parse_dt(e["date_end"]).date())]
        circuit = e.get("circuit") or {}
        events.append(Event(key=f"{start.year}-{e['shortname'].lower()}", season=start.year, name=gp_name(e["name"]),
                            venue=circuit.get("name", "").strip(), country=(circuit.get("country") or "").strip(),
                            start_day=start.date(), end_day=parse_dt(e["date_end"]).date(), url=SITE,
                            sessions=sorted(sessions, key=lambda s: s.start or parse_dt(e["date_start"]))))
    return events


def parse_riders(data: list, series_id: str, season: int) -> list[Entry]:
    name = CLASS[series_id][1]
    out = []
    for r in data:
        step = r.get("current_career_step") or {}
        if step.get("season") != season or (step.get("category") or {}).get("name") != name:
            continue
        team = step.get("sponsored_team") or (step.get("team") or {}).get("name", "")
        out.append(Entry(f"{r['name']} {r['surname']}".strip(), team, str(step.get("number") or "")))
    return out


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events, covered = [], []
    for year in seasons:
        try:
            found = parse_events(await get_json(f"{API}/events?seasonYear={year}"), series_id)
        except Exception:
            if year == seasons[0]:
                raise
            continue  # next season's calendar isn't out yet
        if found:
            events += found
            covered.append(year)
    entries = parse_riders(await get_json(f"{API}/riders"), series_id, seasons[0])
    return AdapterResult(events, covered, entries)


# ---- standings and the last race -------------------------------------------------------------------
def parse_standings(data: dict) -> list[StandingRow]:
    return [StandingRow(r["position"], r["rider"]["full_name"], (r.get("team") or {}).get("name", ""), r.get("points") or 0)
            for r in data.get("classification") or [] if r.get("position")]


def _gap(r: dict) -> str:
    gap = r.get("gap") or {}
    if gap.get("lap") not in (None, "", "0"):
        laps = int(gap["lap"])
        return f"+{laps} lap{'s' if laps > 1 else ''}"
    return f"+{gap['first']}" if gap.get("first") else ""


def parse_classification(data: dict) -> list[ResultRow]:
    rows = []
    for r in data.get("classification") or []:
        team = (r.get("team") or {}).get("name", "")
        name = r["rider"]["full_name"]
        if not r.get("position") or r.get("status") == "OUTSTND":
            laps = r.get("total_laps") or 0
            rows.append(ResultRow("DNF", name, team, f"{laps} lap{'' if laps == 1 else 's'}"))
        elif r["position"] == 1:
            rows.append(ResultRow("1", name, team, r.get("time") or "", r.get("points") or 0))
        else:
            rows.append(ResultRow(str(r["position"]), name, team, _gap(r), r.get("points") or 0))
    return rows


async def standings(series_id: str) -> Standings | None:
    seasons = await get_json(f"{API}/results/seasons")
    season = next((s for s in seasons if s.get("current")), None)
    if not season:
        return None
    cats = await get_json(f"{API}/results/categories?seasonUuid={season['id']}")
    want = CLASS[series_id][1]
    cat = next((c for c in cats if re.sub(r"[^\w]", "", c["name"]) == want), None)
    if not cat:
        return None
    st = Standings(drivers=parse_standings(
        await get_json(f"{API}/results/standings?seasonUuid={season['id']}&categoryUuid={cat['id']}")),
        season_label=f"{season['year']} {want} World Championship")
    finished = [e for e in await get_json(f"{API}/results/events?seasonUuid={season['id']}&isFinished=true")
                if not e.get("test")]
    finished.sort(key=lambda e: e.get("date_start") or "")
    for e in reversed(finished):
        sessions = await get_json(f"{API}/results/sessions?eventUuid={e['id']}&categoryUuid={cat['id']}")
        races = [s for s in sessions if re.match(r"RAC\d?$", s.get("type") or "")]
        if not races:
            continue
        race = races[-1]
        rows = parse_classification(await get_json(f"{API}/results/session/{race['id']}/classification?test=false"))
        if rows:
            st.last_event = gp_name(e.get("sponsored_name") or e.get("name") or "")
            st.last_session = SESSION.get(race["type"], ("Race",))[0]
            st.last_results = rows
            break
    return st

