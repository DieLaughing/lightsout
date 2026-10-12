"""WRC: each season's calendar page lists every rally with its dates and country.

(The per-rally pages are sometimes served as an empty shell, so we don't rely on them.)
Rallies become one calendar entry per day, the agreed low-noise format.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta

from ..models import RALLY_DAY, AdapterResult, Entry, Event, ResultRow, Session, StandingRow, Standings
from .base import get_json, get_text, tidy_caps

SITE = "https://www.wrc.com"
# The JSON service behind wrc.com's results pages (also used for live rally timing).
API = "https://p-p.redbull.com/rb-wrccom-lintegration-yv-prod/api"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# "21 – 24 January 2027", "29 July – 1 August 2027", "30 September – 3 October 2027"
RANGE = re.compile(r"(\d{1,2})(?:\s+([A-Za-z]+))?\s*[–-]\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")


def _date(d: str, month: str, year: str) -> date:
    return datetime.strptime(f"{d} {month} {year}", "%d %B %Y").date()


def parse_calendar(page: str, season: int) -> list[Event]:
    events = {}
    for slug, inner in re.findall(rf'<a[^>]+href="/en/events/(wrc-[a-z0-9-]+-{season})"[^>]*>(.*?)</a>', page, re.S):
        parts = [p.strip() for p in html.unescape(re.sub(r"<[^>]+>", "\n", inner)).split("\n") if p.strip()]
        m = next((RANGE.search(p) for p in parts if RANGE.search(p)), None)
        if not m or slug in events:
            continue  # the page links each rally twice; only the card carries dates
        d1, m1, d2, m2, year = m.groups()
        first, last = _date(d1, m1 or m2, year), _date(d2, m2, year)
        if first > last:  # "30 December – 2 January" style wrap
            first = first.replace(year=first.year - 1)
        date_idx = next(i for i, p in enumerate(parts) if RANGE.search(p))
        country = parts[date_idx + 1] if date_idx + 1 < len(parts) else ""
        name = re.sub(rf"^WRC\s+|\s+{season}$", "", parts[0]).strip()
        sessions = []
        for n in range((last - first).days + 1):
            d = first + timedelta(days=n)
            sessions.append(Session(f"day{n + 1}", f"Day {n + 1} ({WEEKDAYS[d.weekday()]})", RALLY_DAY, day=d))
        events[slug] = Event(key=f"{season}-{slug.removesuffix(f'-{season}')}", season=season, name=name,
                             country=country, url=f"{SITE}/en/events/{slug}", sessions=sessions)
    return list(events.values())


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    latest = await get_text(f"{SITE}/en/calendar")  # the newest season lives here, older ones at /wrc-YYYY
    events, covered = [], []
    for season in seasons:
        season_events = parse_calendar(latest, season)
        if not season_events:
            try:
                season_events = parse_calendar(await get_text(f"{SITE}/en/calendar/wrc-{season}"), season)
            except Exception:
                continue
        if season_events:
            events += season_events
            covered.append(season)
    try:
        await attach_entry_lists(events)
    except Exception:
        pass  # entry lists are a bonus; the calendar stands on its own
    try:
        entries = await registered_drivers()
    except Exception:
        entries = []
    return AdapterResult(events, covered, entries)


async def registered_drivers() -> list[Entry]:
    """Everyone registered for this season's WRC driver championships (WRC, WRC2, WRC3, Junior...).
    Registered isn't entered: crews still pick their rallies, so this only says "races WRC this year"."""
    year = datetime.now().year
    sid = await season_id(year) or await season_id(year - 1)
    if not sid:
        return []
    detail = await get_json(f"{API}/season-detail.json?seasonId={sid}")
    champs = {c["name"]: c["championshipId"] for c in detail.get("championships") or []}
    people: dict[str, Entry] = {}
    for api_name, _label in CHAMPIONSHIPS:
        if api_name not in champs:
            continue
        cd = await get_json(f"{API}/championship-detail.json?championshipId={champs[api_name]}&seasonId={sid}")
        for e in cd.get("championshipEntries") or []:
            name = f"{e.get('fieldOne', '')} {(e.get('fieldTwo') or '').title()}".strip()
            if name and name not in people:
                people[name] = Entry(name, tidy_caps(e.get("fieldFive") or "") or tidy_caps(e.get("fieldFour") or ""))
    return list(people.values())


async def attach_entry_lists(events: list[Event], today: date | None = None) -> None:
    """Rally crews choose their events, so "who's in" comes from each rally's published entry list.
    Lists appear a few weeks before the rally; until then `entrants` stays None ("not out yet")."""
    by_start = {e.sessions[0].day.isoformat(): e for e in events if e.sessions}
    today = today or datetime.now().date()
    for year in sorted({e.season for e in events}):
        sid = await season_id(year)
        if not sid:
            continue
        detail = await get_json(f"{API}/season-detail.json?seasonId={sid}")
        for r in detail.get("seasonRounds") or []:
            ev = r.get("event") or {}
            ours = by_start.get(ev.get("startDate", ""))
            if not ours or ev.get("finishDate", "0") < (today - timedelta(days=3)).isoformat():
                continue
            info = await get_json(f"{API}/events/{ev['eventId']}.json")
            rally = next((x for x in info.get("rallies") or [] if x.get("isMain")), None)
            if not rally:
                continue
            entries = await get_json(f"{API}/events/{ev['eventId']}/rallies/{rally['rallyId']}/entries.json")
            if entries:
                ours.entrants = sorted({person(e.get("driver")) for e in entries} | {person(e.get("codriver")) for e in entries} - {""})


def ms_to_time(ms: int | None, plus: bool = False) -> str:
    if ms is None:
        return ""
    s, ms = divmod(int(ms), 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    text = f"{h}:{m:02d}:{s:02d}.{ms // 100}" if h else f"{m}:{s:02d}.{ms // 100}" if m else f"{s}.{ms // 100}"
    return f"+{text}" if plus else text


def person(p: dict | None) -> str:
    """"Elfyn EVANS" -> "Elfyn Evans" (WRC writes surnames in capitals)."""
    if not p:
        return ""
    return f"{p.get('firstName', '')} {(p.get('lastName') or '').title()}".strip()


async def season_id(year: int) -> int | None:
    seasons = await get_json(f"{API}/seasons.json")
    return next((s["seasonId"] for s in seasons if s.get("year") == year and "World Rally" in s.get("name", "")), None)


# Which championships to show: (name in the API, label in the app). Drivers first = the main table.
CHAMPIONSHIPS = [
    ("FIA World Rally Championship for Drivers", "WRC"),
    ("FIA WRC2 Championship for Drivers", "WRC2"),
    ("FIA WRC3 Championship for Drivers", "WRC3"),
    ("FIA Junior WRC Championship for Drivers", "Junior WRC"),
    ("FIA WRC Masters Cup for Drivers", "Masters Cup"),
]
TEAMS = "FIA World Rally Championship for Manufacturers"


async def _table(champ_id: int, season: int, kind: str) -> list[StandingRow]:
    detail = await get_json(f"{API}/championship-detail.json?championshipId={champ_id}&seasonId={season}")
    who = {e["championshipEntryId"]: e for e in detail.get("championshipEntries") or []}
    res = await get_json(f"{API}/championship-overall-results.json?championshipId={champ_id}&seasonId={season}")
    rows = []
    for r in res.get("entryResults") or []:
        e = who.get(r["championshipEntryId"], {})
        if kind == "drivers":
            name = f"{e.get('fieldOne', '')} {(e.get('fieldTwo') or '').title()}".strip()
            team = tidy_caps(e.get("fieldFive") or "") or tidy_caps(e.get("fieldFour") or "")
        else:
            name, team = tidy_caps(e.get("fieldOne") or ""), ""
        rows.append(StandingRow(r["overallPosition"], name, team, r.get("overallPoints") or 0))
    return sorted(rows, key=lambda x: x.pos)


async def last_rally_results(season: int, detail: dict) -> tuple[str, list[ResultRow]]:
    today = datetime.now().date().isoformat()
    done = [r["event"] for r in detail.get("seasonRounds") or [] if (r.get("event") or {}).get("finishDate", "9") < today]
    if not done:
        return "", []
    ev = max(done, key=lambda e: e["finishDate"])
    event = await get_json(f"{API}/events/{ev['eventId']}.json")
    rally = next((r for r in event.get("rallies") or [] if r.get("isMain")), (event.get("rallies") or [{}])[0])
    entries = {e["entryId"]: e for e in await get_json(f"{API}/events/{ev['eventId']}/rallies/{rally['rallyId']}/entries.json")}
    rows = []
    for r in await get_json(f"{API}/events/{ev['eventId']}/rallies/{rally['rallyId']}/results.json"):
        e = entries.get(r["entryId"], {})
        rows.append(ResultRow(str(r.get("position") or ""), person(e.get("driver")),
                              " · ".join(x for x in ((e.get("group") or {}).get("name", ""),
                                                       tidy_caps((e.get("entrant") or e.get("manufacturer") or {}).get("name", ""))) if x),
                              ms_to_time(r.get("totalTimeMs")) if r.get("position") == 1 else ms_to_time(r.get("diffFirstMs"), plus=True),
                              code=(e.get("driver") or {}).get("code", "")))
    return ev["name"], rows


async def standings(series_id: str) -> Standings | None:
    year = datetime.now().year
    for y in (year, year - 1):
        sid = await season_id(y)
        if sid:
            break
    else:
        return None
    detail = await get_json(f"{API}/season-detail.json?seasonId={sid}")
    champs = {c["name"]: c["championshipId"] for c in detail.get("championships") or []}
    st = Standings(season_label=f"{y}")
    for i, (api_name, label) in enumerate(CHAMPIONSHIPS):
        if api_name not in champs:
            continue
        rows = await _table(champs[api_name], sid, "drivers")
        if i == 0:
            st.drivers = rows
        elif rows:
            st.classes[label] = rows
    if TEAMS in champs:
        st.teams = await _table(champs[TEAMS], sid, "teams")
    st.last_event, st.last_results = await last_rally_results(y, detail)
    st.last_session = "Final classification" if st.last_results else ""
    return st
