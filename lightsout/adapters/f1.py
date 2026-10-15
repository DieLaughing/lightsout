"""Formula 1 schedule from Jolpica (the Ergast successor)."""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import (PRACTICE, QUALIFYING, RACE, SPRINT, AdapterResult, Entry, Event, ResultRow, Session,
                      StandingRow, Standings)
from .base import get_json

API = "https://api.jolpi.ca/ergast/f1"

# Jolpica field -> (session key, display name, kind)
SESSION_FIELDS = [
    ("FirstPractice", "fp1", "Practice 1", PRACTICE),
    ("SecondPractice", "fp2", "Practice 2", PRACTICE),
    ("ThirdPractice", "fp3", "Practice 3", PRACTICE),
    ("SprintQualifying", "sq", "Sprint Qualifying", QUALIFYING),
    ("SprintShootout", "sq", "Sprint Shootout", QUALIFYING),
    ("Sprint", "sprint", "Sprint", SPRINT),
    ("Qualifying", "q", "Qualifying", QUALIFYING),
]


def _when(block: dict) -> datetime | None:
    if not block.get("time"):
        return None
    return datetime.fromisoformat(f"{block['date']}T{block['time'].replace('Z', '+00:00')}")


def parse_season(data: dict) -> list[Event]:
    events = []
    for race in data["MRData"]["RaceTable"]["Races"]:
        season = int(race["season"])
        sessions = []
        for field, key, name, kind in SESSION_FIELDS:
            if field in race:
                start = _when(race[field])
                sessions.append(Session(key, name, kind, start=start,
                                        day=None if start else datetime.fromisoformat(race[field]["date"]).date()))
        start = _when(race)
        sessions.append(Session("race", "Race", RACE, start=start,
                                day=None if start else datetime.fromisoformat(race["date"]).date()))
        loc = race["Circuit"]["Location"]
        events.append(Event(
            key=f"{season}-r{int(race['round']):02d}",
            season=season,
            name=race["raceName"],
            venue=race["Circuit"]["circuitName"],
            country=loc.get("country", ""),
            url=race.get("url", ""),
            sessions=sessions,
        ))
    return events


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events, covered = [], []
    for season in seasons:
        data = await get_json(f"{API}/{season}.json?limit=100")
        season_events = parse_season(data)
        if season_events:
            events += season_events
            covered.append(season)
    entries = []
    if covered:
        standings = await get_json(f"{API}/{covered[-1]}/driverstandings.json")
        lists = standings["MRData"]["StandingsTable"]["StandingsLists"]
        for row in lists[0]["DriverStandings"] if lists else []:
            d = row["Driver"]
            team = row["Constructors"][-1]["name"] if row.get("Constructors") else ""
            entries.append(Entry(f"{d['givenName']} {d['familyName']}", team, d.get("permanentNumber", "")))
    return AdapterResult(events, covered, entries)


def _name(d: dict) -> str:
    return f"{d['givenName']} {d['familyName']}"


async def standings(series_id: str) -> Standings | None:
    year = datetime.now(timezone.utc).year
    for season in (year, year - 1):  # early in the year the new season has no points yet
        data = await get_json(f"{API}/{season}/driverstandings.json")
        lists = data["MRData"]["StandingsTable"]["StandingsLists"]
        if lists:
            break
    else:
        return None
    st = Standings(season_label=f"{season} · after round {lists[0]['round']}")
    for row in lists[0]["DriverStandings"]:
        d = row["Driver"]
        st.drivers.append(StandingRow(int(row["position"]), _name(d), row["Constructors"][-1]["name"]
                                      if row.get("Constructors") else "", float(row["points"]), d.get("code", ""),
                                      int(row.get("wins") or 0)))
    teams = (await get_json(f"{API}/{season}/constructorstandings.json"))["MRData"]["StandingsTable"]["StandingsLists"]
    for row in teams[0]["ConstructorStandings"] if teams else []:
        st.teams.append(StandingRow(int(row["position"]), row["Constructor"]["name"], points=float(row["points"]),
                                    wins=int(row.get("wins") or 0)))
    races = (await get_json(f"{API}/{season}/last/results.json"))["MRData"]["RaceTable"]["Races"]
    if races:
        st.last_event, st.last_session = races[0]["raceName"], "Race"
        for r in races[0]["Results"]:
            time = (r.get("Time") or {}).get("time") or r.get("status", "")
            st.last_results.append(ResultRow(r["positionText"], _name(r["Driver"]), r["Constructor"]["name"],
                                             time, float(r["points"]), r["Driver"].get("code", "")))
    return st


async def history(series_id: str, season: int, have: set[str]) -> list[dict]:
    """Every race of the season so far: [{event, day, rows: {name: [pos, points]}}] (sprints left out)."""
    races, offset, total = {}, 0, 1
    while offset < total:
        data = (await get_json(f"{API}/{season}/results.json?limit=100&offset={offset}"))["MRData"]
        total, offset = int(data["total"]), offset + 100
        for race in data["RaceTable"]["Races"]:  # a race can straddle two pages
            r = races.setdefault(race["raceName"], {"event": race["raceName"], "day": race["date"], "rows": {}})
            for x in race.get("Results") or []:
                r["rows"][_name(x["Driver"])] = [x["positionText"], float(x["points"])]
    return list(races.values())
