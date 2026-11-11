"""IMSA WeatherTech SportsCar Championship from imsa.com (Astro site since Nov 2026; class names are hashed, so
read the structure and the aria/data attributes, not the classes).

  /<year>/schedule              every series' calendar for that season; a 404 = not published yet (the 2026 page is
                                gone since the rebuild, so a finished season just stays as stored). The series radios
                                (value="weathertech" data-series-index="N") pick the <ul data-series-index="N">; one
                                <article> per event: <h3> name, first <p> venue, <p aria-label="January 22, 2027
                                through January 24, 2027">, a "Schedule" link /events/<year>-<slug>?tab=schedule&series=weathertech
  /events/<year>-<slug>         the weekend schedule (tab=schedule) is posted a few weeks before each event; until
                                parse_weekend() finds sessions there the event shows as one race weekend.
  /wp-admin/admin-ajax.php      POST action=getImsaStandings (standings=driver|team, seriesId=1, currentYear, classId):
                                the standings tables; class ids from the site's own galaxy API (/classes?series_id=1).
                                From the old WordPress site; check it again when the 2027 season starts.
"""
from __future__ import annotations

import html
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from curl_cffi.requests.exceptions import HTTPError

from ..models import PRACTICE, RACE, AdapterResult, Entry, Event, Session, StandingRow, Standings, kind_from_name
from .base import _client, get_json, get_text, html_tables, slugify
from .gtwce import crews

SITE = "https://www.imsa.com"
GALAXY = "https://dvw6yynr86g3k.cloudfront.net/galaxy/api"
ET = ZoneInfo("America/New_York")
SERIES_NAME = "WeatherTech"
_recent: tuple[float, Standings | None] | None = None  # fetch() reads the standings for the grid; refresh asks again


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def season_url(year: int) -> str:
    return f"{SITE}/{year}/schedule"


def _date(label: str) -> date:
    return datetime.strptime(label.strip(), "%B %d, %Y").date()


def parse_season(page: str, year: int) -> list[dict]:
    radio = re.search(r'<input\b[^>]*value="weathertech"[^>]*data-series-index="(\d+)"', page)
    if not radio:
        return []
    start = re.search(rf'<ul\b[^>]*data-series-index="{radio.group(1)}"[^>]*>', page)
    if not start:
        return []
    rest = page[start.end():]
    stop = re.search(r'<ul\b[^>]*data-series-index="\d+"|</section>', rest)
    out = []
    for card in re.findall(r"<article\b.*?</article>", rest[:stop.start()] if stop else rest, re.S):
        name = re.search(r"<h3\b[^>]*>(.*?)</h3>", card, re.S)
        venue = re.search(r"<hgroup>.*?<p\b[^>]*>(.*?)</p>", card, re.S)
        days = re.search(r'aria-label="([A-Z][a-z]+ \d{1,2}, \d{4})(?: through ([A-Z][a-z]+ \d{1,2}, \d{4}))?"', card)
        if not (name and days):
            continue
        link = re.search(r'href="([^"]*\?tab=schedule[^"]*)"', card)
        first = _date(days.group(1))
        out.append({"name": _text(name.group(1)), "venue": _text(venue.group(1)) if venue else "", "start": first,
                    "end": _date(days.group(2)) if days.group(2) else first,
                    "url": SITE + html.unescape(link.group(1)) if link else ""})
    return out


def _at(day: date, clock: str) -> datetime:
    return datetime.strptime(f"{day.isoformat()} {clock.strip().upper()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=ET)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def parse_weekend(page: str, event_name: str) -> list[Session]:
    """WeatherTech sessions from an event page's weekend schedule (times are US Eastern). Practice and qualifying say
    "WeatherTech Championship"; the race is listed under the event's own name ("Motul Petit Le Mans"), and the 24 at
    Daytona is split at midnight into two entries, joined back here."""
    sessions, day = [], None
    for header, clock, name in re.findall(
            r'class="day-event-header">(.*?)</div>|class=\'event-time\'>(.*?)</div>\s*<div class=\'event-name\'>(.*?)</div>', page, re.S):
        if header:
            try:
                day = datetime.strptime(_text(header), "%A, %B %d, %Y").date()
            except ValueError:
                day = None
            continue
        name = _text(name)
        is_race = _norm(name) == _norm(event_name)
        if not day or not (is_race or SERIES_NAME in name):
            continue
        label = "Race" if is_race else re.sub(r"\s*-?\s*WeatherTech Championship\s*-?\s*", " ", name).strip()
        m = re.match(r"(\d{1,2}:\d{2} [AP]M)(?: to (\d{1,2}:\d{2} [AP]M))?", _text(clock), re.I)
        if not m:
            continue
        start = _at(day, m.group(1))
        end = _at(day, m.group(2)) if m.group(2) else None
        if end and end <= start:
            end += timedelta(days=1)
        prev = sessions[-1] if sessions else None
        if prev and prev.name == label and prev.end and start - prev.end <= timedelta(minutes=2):
            prev.end = end or prev.end  # the second half of a race that runs past midnight
            continue
        sessions.append(Session(key=slugify(f"{day.isoformat()} {label}"), name=label,
                                kind=RACE if is_race else kind_from_name(label), start=start, end=end))
    return sessions


def race_length(duration: str) -> timedelta | None:
    m = re.match(r"(\d+)\s*(Hour|Minute)", duration, re.I)
    if not m:
        return None
    n = int(m.group(1))
    return timedelta(hours=n) if m.group(2).lower().startswith("hour") else timedelta(minutes=n)


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events, covered = [], []
    for year in seasons:
        try:
            page = await get_text(season_url(year))
        except HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                continue  # that season's calendar isn't out (or, since the rebuild, no longer online)
            raise
        found = parse_season(page, year)
        if not found:
            raise RuntimeError(f"no WeatherTech events on {season_url(year)}: has the page changed?")
        for ev in found:
            sessions = []
            if ev["url"]:
                try:
                    sessions = parse_weekend(await get_text(ev["url"]), ev["name"])
                except Exception:
                    pass  # the weekend schedule isn't posted yet
            length = race_length(ev["name"])  # "12 Hours of Sebring"; the new site has no duration field
            for s in sessions:
                if s.kind == RACE and s.end is None and length:
                    s.end = s.start + length
            if not sessions:  # the Roar is the pre-season test: practice, not a race
                test = "Roar" in ev["name"]
                sessions = [Session(key="weekend", name="Test weekend" if test else "Race weekend",
                                    kind=PRACTICE if test else RACE, day=ev["start"], end_day=ev["end"])]
            event_url = ev["url"].split("?")[0]
            events.append(Event(key=f"{year}-{slugify(ev['name'])}", season=year, name=ev["name"], venue=ev["venue"],
                                country="USA", start_day=ev["start"], end_day=ev["end"], url=event_url, sessions=sessions))
        covered.append(year)
    entries = []
    try:
        st = await standings(series_id)
        for rows in [st.drivers, *st.classes.values()]:
            for r in rows:
                entries += [Entry(n.strip(), r.team) for n in r.name.split(" / ")]
    except Exception:
        pass  # standings are how the grid is known; the schedule still saves without them
    return AdapterResult(events, covered, list({e.name: e for e in entries}.values()))


# ---- standings ----------------------------------------------------------------------------------------------
async def _table(kind: str, year: int, class_id: int) -> list[tuple[int, str, float]]:
    r = await _client().post(f"{SITE}/wp-admin/admin-ajax.php", data={
        "action": "getImsaStandings", "standings": kind, "seriesId": "1", "currentYear": str(year), "classId": str(class_id)})
    r.raise_for_status()
    return parse_standings(r.text)


def parse_standings(page: str) -> list[tuple[int, str, float]]:
    """The table with points (the first one is names only): rank, name, ... , Total."""
    table = next((t for t in html_tables(page) if t and t[0] and t[0][-1].strip().lower() == "total"), [])
    out = []
    for row in table[1:]:
        if len(row) >= 3 and row[0].isdigit():
            try:
                out.append((int(row[0]), row[1], float(row[-1] or 0)))
            except ValueError:
                continue
    return out


async def standings(series_id: str) -> Standings | None:
    global _recent
    if _recent and time.monotonic() - _recent[0] < 600:
        return _recent[1]
    _recent = (time.monotonic(), await _standings())
    return _recent[1]


async def _standings() -> Standings | None:
    year = datetime.now().year
    classes = await get_json(f"{GALAXY}/classes?series_id=1&season_id={year}")
    if not classes:
        return None
    st = Standings(season_label=f"{year} WeatherTech Championship")
    for i, c in enumerate(classes):
        drivers = crews(await _table("driver", year, c["id"]))
        if i == 0:
            st.drivers = drivers
            st.teams = [StandingRow(p, re.sub(r"^#\d+\s+", "", n), "", pts) for p, n, pts in await _table("team", year, c["id"])]
            st.season_label = f"{year} WeatherTech Championship · {c.get('shortcode') or c.get('name')}"
        else:
            st.classes[c.get("shortcode") or c.get("name")] = drivers
    return st if st.drivers else None
