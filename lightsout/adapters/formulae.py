"""Formula E: the calendar page's schema.org list gives each round's date.

Session times aren't in the page (they load from a GraphQL API we haven't mapped
yet), so each round is a race-day entry with the time to be announced.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..models import RACE, AdapterResult, Entry, Event, ResultRow, Session, StandingRow, Standings
from .base import day, get_text, html_tables, json_ld, slugify, tidy_caps

URL = "https://www.fiaformulae.com/en/calendar"
DRIVERS_URL = "https://www.fiaformulae.com/en/drivers"


def parse_drivers(page: str) -> list[Entry]:
    out = []
    for block in json_ld(page):
        if block.get("@type") == "ItemList":
            for item in block.get("itemListElement", []):
                p = item.get("item") or {}
                if p.get("@type") == "Person":
                    out.append(Entry(p["name"], (p.get("memberOf") or {}).get("name", "")))
    return out


def parse_calendar(page: str) -> list[Event]:
    events = []
    for block in json_ld(page):
        if block.get("@type") != "ItemList":
            continue
        for item in block.get("itemListElement", []):
            ev = item["item"]
            start = day(ev["startDate"])
            season = start.year if start.month >= 7 else start.year - 1  # seasons run Dec-Aug
            addr = (ev.get("location") or {}).get("address") or {}
            events.append(Event(
                key=f"s{season}-{slugify(ev['name'])}", season=season, name=ev["name"].split(" ", 1)[-1],
                venue=(ev.get("location") or {}).get("name", ""), country=addr.get("addressCountry", ""),
                url=URL,
                sessions=[Session("race", "Race", RACE, day=start)],
            ))
    return events


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events = parse_calendar(await get_text(URL))
    try:
        entries = parse_drivers(await get_text(DRIVERS_URL))
    except Exception:
        entries = []
    return AdapterResult(events, sorted({e.season for e in events}), entries)


RESULTS = "https://www.fiaformulae.com/en/results-and-standings"


def _num(text: str) -> float:
    m = re.search(r"[\d.]+", text or "")
    return float(m.group()) if m else 0


def parse_driver_standings(page: str) -> list[StandingRow]:
    tables = html_tables(page)
    if not tables:
        return []
    rows = []
    for r in tables[0][1:]:
        if len(r) < 4 or not r[0].isdigit():
            continue
        team = r[2]
        # The driver cell runs name, 3-letter nationality and team together: "Pascal WehrleinDEUPORSCHE ..."
        name = re.sub(r"[A-Z]{3}$", "", r[1].removesuffix(team)).strip()
        rows.append(StandingRow(int(r[0]), name, tidy_caps(team), _num(r[-1])))
    return rows


def parse_team_standings(page: str) -> list[StandingRow]:
    tables = html_tables(page)
    return [StandingRow(int(r[0]), tidy_caps(r[1]), points=_num(r[-1]))
            for r in (tables[0][1:] if tables else []) if len(r) >= 3 and r[0].isdigit()]


async def standings(series_id: str) -> Standings | None:
    # Season 1 was 2014/15; a season starting in year Y is number Y - 2013. Before the new season's
    # first race its table is empty, so fall back to the previous season.
    now = datetime.now(timezone.utc)
    current = (now.year if now.month >= 7 else now.year - 1) - 2013
    for season in (current, current - 1):
        drivers = parse_driver_standings(await get_text(f"{RESULTS}?season={season}&tab=drivers"))
        if drivers:
            break
    else:
        return None
    st = Standings(drivers=drivers, season_label=f"Season {season}",
                   teams=parse_team_standings(await get_text(f"{RESULTS}?season={season}&tab=teams")))
    # The default view of the season page is its most recent race result.
    page = await get_text(f"{RESULTS}?season={season}")
    table = next((t for t in html_tables(page) if t and "Grid" in " ".join(t[0])), None)
    rounds = re.findall(r"round=(\d+)-([a-z-]+)", page)
    if rounds:
        n, slug = max(rounds, key=lambda r: int(r[0]))
        st.last_event = f"Round {n} · {slug.replace('-', ' ').title()}"
        st.last_session = "Race"
    for r in (table or [])[1:]:
        if len(r) < 5 or not r[0][:1].isdigit() and r[0][:2] not in ("DN", "NC", "DS"):
            continue
        team = r[2]
        name = re.sub(r"[A-Z]{3}$", "", r[1].removesuffix(team)).strip()
        st.last_results.append(ResultRow(re.match(r"\d+|[A-Z]+", r[0]).group(), name, tidy_caps(team), r[4], _num(r[-1])))
    return st
