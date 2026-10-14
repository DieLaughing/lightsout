"""WorldSBK (World Superbike): the public JSON API behind worldsbk.com (api.wsbk.pulselive.com, JSON:API style:
`data` plus `included` for riders and teams).

  /wsbk-events/v1/seasons/<year>/rounds                        rounds: id 2026-JER, source_id JER, dates, circuit
  /wsbk-events/v1/seasons/<year>/rounds/<source_id>/sessions   every class's sessions (UTC); category SBK = WorldSBK
  /wsbk-riders/v1/seasons/<year>/categories/SBK/teams          teams, riders in `included`
  /wsbk-results/v1/seasons/<year>/categories/SBK/riders/standings
  /wsbk-results/v1/seasons/<year>/categories/SBK/rounds/<source_id>/sessions/<session source_id>/results
                                                               position, laps, time in ms, status (Classified/Retired)
Support classes (WorldSSP, World Sportbike, Women's) share the feeds; only WorldSBK is on the schedule for now.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..models import PRACTICE, QUALIFYING, RACE, SPRINT, AdapterResult, Entry, Event, ResultRow, Session, StandingRow, Standings
from .base import get_json, parse_dt

API = "https://api.wsbk.pulselive.com"
SITE = "https://www.worldsbk.com/en/calendar/event/"
HEADERS = {"x-client": "FE", "Referer": "https://www.worldsbk.com/en/calendar"}
CLASS = "SBK"
LENGTH = {RACE: timedelta(minutes=40), SPRINT: timedelta(minutes=20)}  # the feed leaves end times empty
COUNTRIES = {"ESP": "Spain", "ITA": "Italy", "GBR": "UK", "FRA": "France", "PRT": "Portugal", "AUS": "Australia",
             "NLD": "Netherlands", "CZE": "Czechia", "HUN": "Hungary", "DEU": "Germany", "IDN": "Indonesia",
             "ARG": "Argentina", "USA": "USA", "JPN": "Japan", "TUR": "Turkey", "POL": "Poland"}


async def _get(path: str):
    return await get_json(API + path, headers=HEADERS)


def kind(name: str) -> str:
    n = name.lower()
    if "superpole race" in n:
        return SPRINT
    if "superpole" in n:
        return QUALIFYING
    if n.startswith("race"):
        return RACE
    return PRACTICE


def parse_sessions(data: list) -> list[Session]:
    out = []
    for s in data:
        if s["relationships"]["category"]["data"]["id"] != CLASS:
            continue
        a = s["attributes"]
        name = a.get("brief_description") or a.get("description") or a.get("short_name")
        if not a.get("start_date_utc"):
            out.append(Session(key=s["id"].lower(), name=name, kind=kind(name), day=date.fromisoformat(a["circuit_date"])))
            continue
        k, start = kind(name), parse_dt(a["start_date_utc"])
        end = parse_dt(a["end_date_utc"]) if a.get("end_date_utc") else start + LENGTH.get(k, timedelta(minutes=45))
        out.append(Session(key=s["id"].lower(), name=name, kind=k, start=start, end=end))
    return sorted(out, key=lambda x: x.start.isoformat() if x.start else x.day.isoformat())


def parse_round(r: dict, sessions: list[Session]) -> Event:
    a = r["attributes"]
    start, end = date.fromisoformat(a["start_date"][:10]), date.fromisoformat(a["end_date"][:10])
    if not sessions:  # timetable not out yet
        sessions = [Session(key="weekend", name="Race weekend", kind=RACE, day=start, end_day=end)]
    return Event(key=r["id"].lower(), season=start.year, name=a.get("description") or a["name"], venue=a["name"],
                 country=COUNTRIES.get(a.get("country_iso") or "", a.get("country_iso") or ""),
                 start_day=start, end_day=end, url=SITE + r["id"], sessions=sessions)


def parse_riders(teams: dict) -> list[Entry]:
    riders = {x["id"]: x["attributes"] for x in teams.get("included") or [] if x["type"] == "riders"}
    out = []
    for t in teams.get("data") or []:
        for ref in t["relationships"].get("riders", {}).get("data") or []:
            r = riders.get(ref["id"])
            if r:
                out.append(Entry(f"{r['name']} {r['surname']}".strip(), t["attributes"]["name"], str(r.get("number") or "")))
    return out


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events, covered = [], []
    for year in seasons:
        rounds = (await _get(f"/wsbk-events/v1/seasons/{year}/rounds")).get("data") or []
        for r in sorted(rounds, key=lambda r: r["attributes"]["start_date"]):
            sessions = (await _get(f"/wsbk-events/v1/seasons/{year}/rounds/{r['attributes']['source_id']}/sessions")).get("data") or []
            events.append(parse_round(r, parse_sessions(sessions)))
        if rounds:
            covered.append(year)
    entries = parse_riders(await _get(f"/wsbk-riders/v1/seasons/{seasons[0]}/categories/{CLASS}/teams"))
    return AdapterResult(events, covered, entries)


# ---- standings and the last race -------------------------------------------------------------------
def _names(payload: dict) -> tuple[dict, dict]:
    inc = payload.get("included") or []
    riders = {x["id"]: f"{x['attributes']['name']} {x['attributes']['surname']}".strip() for x in inc if x["type"] == "riders"}
    teams = {x["id"]: x["attributes"]["name"] for x in inc if x["type"] == "teams"}
    return riders, teams


def parse_standings(payload: dict) -> list[StandingRow]:
    riders, teams = _names(payload)
    rows = []
    for s in payload.get("data") or []:
        rel = s["relationships"]
        rows.append(StandingRow(s["attributes"]["position"], riders.get(rel["rider"]["data"]["id"], ""),
                                teams.get((rel.get("team") or {}).get("data", {}).get("id"), ""), s["attributes"]["points"]))
    return sorted(rows, key=lambda r: r.pos)


def _clock(ms: int) -> str:
    m, s = divmod(ms / 1000, 60)
    return f"{int(m)}:{s:06.3f}"


def parse_results(payload: dict, known: tuple[dict, dict] = ({}, {})) -> list[ResultRow]:
    """`known` = (riders, teams) by id from the standings: a results payload doesn't always include them."""
    riders, teams = _names(payload)
    riders, teams = {**known[0], **riders}, {**known[1], **teams}
    data = sorted(payload.get("data") or [], key=lambda r: r["attributes"].get("position") or 999)
    if not data:
        return []
    winner = data[0]["attributes"]
    rows = []
    for r in data:
        a, rel = r["attributes"], r["relationships"]
        name = riders.get(rel["rider"]["data"]["id"]) or (f"#{a['number']}" if a.get("number") else "Unknown rider")
        team = teams.get(rel["team"]["data"]["id"], "")
        if a.get("status") != "Classified":
            rows.append(ResultRow("DNF", name, team, f"{a.get('laps') or 0} lap{'' if a.get('laps') == 1 else 's'}"))
        elif a["position"] == 1:
            rows.append(ResultRow("1", name, team, _clock(a["time"])))
        elif a.get("laps") == winner.get("laps"):
            rows.append(ResultRow(str(a["position"]), name, team, f"+{(a['time'] - winner['time']) / 1000:.3f}"))
        else:
            down = (winner.get("laps") or 0) - (a.get("laps") or 0)
            rows.append(ResultRow(str(a["position"]), name, team, f"+{down} lap{'s' if down > 1 else ''}"))
    return rows


async def standings(series_id: str) -> Standings | None:
    now = datetime.now(timezone.utc)
    year = now.year
    payload = await _get(f"/wsbk-results/v1/seasons/{year}/categories/{CLASS}/riders/standings")
    st = Standings(drivers=parse_standings(payload), season_label=f"{year} WorldSBK")
    if not st.drivers:
        return None
    known = _names(payload)
    rounds = sorted((await _get(f"/wsbk-events/v1/seasons/{year}/rounds")).get("data") or [],
                    key=lambda r: r["attributes"]["start_date"], reverse=True)
    for r in rounds:
        if r["attributes"]["start_date"] > now.isoformat():
            continue
        src = r["attributes"]["source_id"]
        sessions = [s for s in (await _get(f"/wsbk-events/v1/seasons/{year}/rounds/{src}/sessions")).get("data") or []
                    if s["relationships"]["category"]["data"]["id"] == CLASS
                    and kind(s["attributes"].get("brief_description") or "") in (RACE, SPRINT)
                    and s["attributes"].get("start_date_utc") and s["attributes"]["start_date_utc"] < now.isoformat()]
        for s in sorted(sessions, key=lambda s: s["attributes"]["start_date_utc"], reverse=True):
            rows = parse_results(await _get(f"/wsbk-results/v1/seasons/{year}/categories/{CLASS}/rounds/{src}"
                                            f"/sessions/{s['attributes']['source_id']}/results"), known)
            if rows:
                st.last_event = r["attributes"].get("description") or r["attributes"]["name"]
                st.last_session = s["attributes"].get("brief_description") or "Race"
                st.last_results = rows
                return st
    return st
