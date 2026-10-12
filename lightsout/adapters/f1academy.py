"""F1 Academy: the calendar page's Next.js data holds every round and session."""
from __future__ import annotations

import json
import re

from ..models import RACE, AdapterResult, Entry, Event, ResultRow, Session, StandingRow, Standings, kind_from_name
from .base import get_text, is_local_midnight, parse_dt

URL = "https://www.f1academy.com/Racing-Series/Calendar"
DRIVERS_URL = "https://www.f1academy.com/Racing-Series/Drivers"
SITE = "https://www.f1academy.com/Racing-Series"


def _next_data(page: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    return json.loads(m.group(1))["props"]["pageProps"]["pageData"]


def parse_drivers(page: str) -> list[Entry]:
    return [Entry(d["FullName"], team["TeamFullName"], str(d.get("CarNumber") or ""))
            for team in _next_data(page).get("Teams") or [] for d in team.get("Drivers") or []]


def parse_calendar(page: str) -> list[Event]:
    data = _next_data(page)
    season = int(data["SeasonStartDate"][:4])
    events = []
    for race in data["Races"]:
        sessions = []
        for s in race.get("Sessions") or []:
            local = parse_dt(s["SessionStartTime"])
            tba = s.get("Unconfirmed") or is_local_midnight(local)
            sessions.append(Session(
                key=str(s["SessionId"]), name=s["SessionName"], kind=kind_from_name(s["SessionName"]),
                start=None if tba else local,
                end=None if tba or not s.get("SessionEndTime") else parse_dt(s["SessionEndTime"]),
                day=local.date() if tba else None,
            ))
        events.append(Event(
            key=f"{season}-r{race['RoundNumber']:02d}", season=season,
            name=race.get("RaceName") or race.get("CircuitShortName") or race.get("CountryName") or f"Round {race['RoundNumber']}",
            venue=race.get("CircuitName") or race.get("CircuitShortName", ""),
            country=race.get("CountryName", ""),
            url=f"https://www.f1academy.com/Racing-Series/Results?raceid={race['RaceId']}",
            sessions=sessions,
        ))
    return events


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events = parse_calendar(await get_text(URL))
    try:
        entries = parse_drivers(await get_text(DRIVERS_URL))
    except Exception:
        entries = []
    return AdapterResult(events, sorted({e.season for e in events}), entries)


def _clean(name: str) -> str:
    return " ".join(name.split())  # the feed pads some names with double spaces


async def standings(series_id: str) -> Standings | None:
    drivers = _next_data(await get_text(f"{SITE}/Standings/Driver"))
    st = Standings(season_label=drivers.get("Season", ""))
    st.drivers = [StandingRow(r["Position"], _clean(r["FullName"]), r.get("TeamName", ""), r["TotalPoints"], r.get("TLA", ""))
                  for r in drivers.get("Standings") or []]
    teams = _next_data(await get_text(f"{SITE}/Standings/Team"))
    st.teams = [StandingRow(r["Position"], _clean(r["FullName"]), points=r["TotalPoints"]) for r in teams.get("Standings") or []]
    # Last race = the most recent race session the calendar says has results.
    cal = _next_data(await get_text(URL))
    done = [(race, s) for race in cal["Races"] for s in race.get("Sessions") or []
            if s.get("SessionResultsAvailable") and kind_from_name(s["SessionName"]) == RACE]
    if done:
        race, session = max(done, key=lambda rs: rs[1]["SessionStartTime"])
        page = _next_data(await get_text(f"{SITE}/Results?raceid={race['RaceId']}"))
        result = next((x for x in page.get("SessionResults") or [] if x["SessionId"] == session["SessionId"]), None)
        if result:
            st.last_event = race.get("CircuitShortName") or race.get("CountryName", "")
            st.last_session = result["SessionName"]
            st.last_results = [ResultRow(r.get("DisplayFinishPosition") or str(r.get("FinishPosition") or ""),
                                         _clean(f"{r['DriverForename']} {r['DriverSurname']}"), r.get("TeamName", ""),
                                         (f"+{r['Gap']}" if r.get("Gap") else r.get("TimeOrFinishReason") or ""),
                                         code=r.get("TLA", ""))
                               for r in result.get("Results") or []]
    return st
