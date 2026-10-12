"""FIA WEC: season page for the round list, then each round's schema.org data."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..models import AdapterResult, Event, Session, kind_from_name
from .base import find_sports_event, get_json, get_text, is_local_midnight, json_ld, parse_dt, slugify

SITE = "https://www.fiawec.com"
# The ACO's API behind 24h-lemans.com (the ACO runs WEC). It only answers requests that look
# like they come from its own site. Entry lists appear here weeks/months before each race.
ACO = "https://api-he.lemans.org/evo/1"
ACO_HEADERS = {"Origin": "https://www.24h-lemans.com"}


def parse_round(page: str, season: int, slug: str, url: str) -> Event | None:
    ev = find_sports_event(json_ld(page))
    if not ev:
        return None
    sessions = []
    for sub in ev.get("subEvent") or []:
        # "Free Practice 1 - Qatar 1812km", "Qualifying - LMGT3 - Qatar 1812km": drop the round name
        parts = sub["name"].split(" - ")
        name = " - ".join(parts[:-1]) if len(parts) > 1 else parts[0]
        local = parse_dt(sub["startDate"])
        tba = is_local_midnight(local)
        sessions.append(Session(slugify(name), name, kind_from_name(name),
                                start=None if tba else local, day=local.date() if tba else None))
    loc = ev.get("location") or {}
    return Event(key=f"{season}-{slug}", season=season,
                 name=ev["name"].removeprefix("WEC ").removesuffix(f" {season}"),
                 venue=loc.get("name", ""), country=str(loc.get("address", "")).split(",")[-1].strip(),
                 url=url, sessions=sessions)


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events, covered = [], []
    for season in seasons:
        page = await get_text(f"{SITE}/en/season/{season}")
        slugs = list(dict.fromkeys(re.findall(rf'/en/race/([a-z0-9-]+-{season})', page)))
        season_events = []
        for slug in slugs:
            url = f"{SITE}/en/race/{slug}"
            ev = parse_round(await get_text(url), season, slug.removesuffix(f"-{season}"), url)
            if ev and ev.sessions:
                season_events.append(ev)
        if season_events:
            events += season_events
            covered.append(season)
    try:
        await attach_entry_lists(events)
    except Exception:
        pass  # entry lists are a bonus; the calendar stands on its own
    return AdapterResult(events, covered)


async def _aco(path: str) -> dict:
    return await get_json(f"{ACO}/{path}", headers=ACO_HEADERS)


async def attach_entry_lists(events: list[Event], today: date | None = None) -> None:
    """Match each WEC round to the ACO's event (by start date) and attach its published entry list."""
    today = today or datetime.now().date()
    for year in sorted({e.season for e in events}):
        aco_events = (await _aco(f"events?year={year}")).get("member") or []
        for ev in (e for e in events if e.season == year):
            first = min((s.day or s.start.date()) for s in ev.sessions) if ev.sessions else None
            last = max((s.day or s.start.date()) for s in ev.sessions) if ev.sessions else None
            if not first or last < today - timedelta(days=3):
                continue
            match = next((a for a in aco_events if a.get("status") != "cancelled"
                          and abs((date.fromisoformat(a["dateStart"][:10]) - first).days) <= 3), None)
            if not match:
                continue
            entries = (await _aco(f"entries?race={match['id']}")).get("member") or []
            names = {d.get("fullname") or f"{d.get('firstname', '')} {d.get('lastname', '')}".strip()
                     for e in entries for d in e.get("drivers") or []}
            if names - {""}:
                ev.entrants = sorted(names - {""})
