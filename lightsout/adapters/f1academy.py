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
    return [Entry(_clean(d["FullName"]), team["TeamFullName"], str(d.get("CarNumber") or ""))
            for team in _next_data(page).get("Teams") or [] for d in team.get("Drivers") or []]


def parse_roster(page: str) -> list[dict]:
    """Who's racing this season, with what the drivers page knows: backer, nationality, wild card, photo."""
    out = []
    for team in _next_data(page).get("Teams") or []:
        for d in team.get("Drivers") or []:
            out.append({"id": d["DriverId"], "name": _clean(d["FullName"]), "wildcard": is_wildcard(d["FullName"]),
                        "code": d.get("TLA", ""), "number": str(d.get("CarNumber") or ""), "team": team["TeamFullName"],
                        "backer": " ".join((d.get("support") or "").split()), "country": d.get("CountryName", ""),
                        "cc": d.get("CountryCode", "")})
    return out


async def roster() -> list[dict]:
    return parse_roster(await get_text(DRIVERS_URL))


# The bio ends with link lists ("### CATCH UP WITH NINA:", "### FIND OUT MORE ABOUT NINA:"): those go.
_LINKS = re.compile(r"^#+\s", re.M)


def _paragraphs(md: str) -> tuple[list[str], str]:
    """The synopsis (light Markdown) as plain paragraphs, plus its pull quote ('> ...') if it has one."""
    body = _LINKS.split(md or "", 1)[0]
    paras, quote = [], ""
    for block in re.split(r"\n\s*\n", body):
        text = " ".join(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", block).replace("__", "").replace("**", "").split())
        if text.startswith(">"):
            quote = quote or text.lstrip("> ").strip()
        elif text:
            paras.append(text)
    return paras, quote


def parse_profile(page: str) -> dict:
    d = _next_data(page)
    c = d.get("Content") or {}
    paras, quote = _paragraphs(c.get("driverSynopsis") or "")
    st = d.get("LatestStandings") or {}
    team = d.get("CurrentDriverTeam") or {}
    return {
        "id": d["DriverId"], "name": _clean(d.get("FullName") or ""), "url": f"{SITE}/Drivers/{d['DriverId']}",
        "bio": paras, "quote": quote, "born": c.get("dateOfBirth") or "", "nationality": d.get("Nationality") or "",
        "country": d.get("CountryName") or "", "backer": " ".join((c.get("support") or "").split()),
        "team": team.get("TeamName") or "", "number": str(team.get("CarNumber") or ""),
        "wildcard": is_wildcard(d.get("FullName") or "") or team.get("Driver_Type") not in (None, "Driver"),
        "pos": st.get("Position"), "points": st.get("TotalPoints"), "season": st.get("SeasonName") or "",
        "teammates": [_clean(m["FullName"]) for m in d.get("TeamMates") or []],
        "photo": ((c.get("driverImage") or {}).get("url") or ""),
        "links": {k: c[f"driver{k.capitalize()}"] for k in ("instagram", "youtube") if c.get(f"driver{k.capitalize()}")},
        "articles": [{"title": " ".join(a["title"].split()), "url": f"https://www.f1academy.com/Latest/{a['id']}/{a['slug']}",
                      "time": a.get("updatedAt") or a.get("realUpdatedAt") or ""} for a in d.get("Articles") or []],
    }


async def profile(driver_id: int) -> dict:
    return parse_profile(await get_text(f"{SITE}/Drivers/{int(driver_id)}/driver"))


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


WILDCARD = re.compile(r"\s*\(WCD\)", re.I)


def is_wildcard(name: str) -> bool:
    return bool(WILDCARD.search(name or ""))


def _clean(name: str) -> str:
    """The feed pads some names with double spaces and marks wild cards 'Alexia Danielsson (WCD)': that's a fact
    about the entry, not the name (follows, tags and profiles match on the name)."""
    return " ".join(WILDCARD.sub("", name).split())


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
