"""FIA European Rallycross (and the Rallycross World Cup) from fia-rallycross.com/calendar.

Each round is a calendar card: event name + country, venue, and dates like "09 - 10 May".
The page shows the current season; a new season appears when the FIA updates it.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta

from ..models import QUALIFYING, RACE, AdapterResult, Event, Session
from .base import get_text, slugify

URL = "https://www.fia-rallycross.com/calendar"
# "09 - 10 May", "30 May - 01 Jun"
DATES = re.compile(r"(\d{1,2})(?:\s+([A-Za-z]+))?\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _date(day: str, month: str, year: int) -> date:
    return datetime.strptime(f"{day} {month[:3]} {year}", "%d %b %Y").date()


def parse_calendar(page: str) -> list[Event]:
    events = []
    # Each section starts with a "<year> CALENDAR" / "<year> FIA Rallycross World Cup" title.
    for section in re.split(r'(?=<h2 class="widget-title">)', page):
        title = re.search(r'<h2 class="widget-title">(.*?)</h2>', section, re.S)
        year = re.search(r"(\d{4})", _text(title.group(1))) if title else None
        if not year:
            continue
        season = int(year.group(1))
        for card in re.findall(r'<li class="more-items-item.*?</li>', section, re.S):
            name_html = re.search(r'<span class="event-name">(.*?)</span>\s*</a>', card, re.S)
            dates = re.search(r'<div class="more-items-long-text">(.*?)</div>', card, re.S)
            if not name_html or not dates or not DATES.search(_text(dates.group(1))):
                continue
            city = re.search(r'<span class="city">(.*?)</span>', name_html.group(1), re.S)
            country = _text(city.group(1)).title() if city else ""
            raw_name = _text(name_html.group(1))
            name = "FIA Rallycross World Cup" if "World Cup" in raw_name or "World Cup" in _text(title.group(1)) \
                else f"Euro RX of {country}"  # drop sponsor prefixes so names (and calendar IDs) stay stable
            venue = re.search(r'<p class="more-items-text">(.*?)</p>', card, re.S)
            d1, m1, d2, m2 = DATES.search(_text(dates.group(1))).groups()
            first, last = _date(d1, m1 or m2, season), _date(d2, m2, season)
            # Rallycross weekends: qualifying heats on day one, semi-finals and final on the last day.
            days = (last - first).days + 1
            sessions = [Session(f"day{n + 1}", "Qualifying heats" if n < days - 1 else "Semi-finals and final",
                                QUALIFYING if n < days - 1 else RACE, day=first + timedelta(days=n))
                        for n in range(days)]
            events.append(Event(key=f"{season}-{slugify(name)}", season=season, name=name,
                                venue=_text(venue.group(1)) if venue else "", country=country,
                                url=URL, sessions=sessions))
    return events


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events = parse_calendar(await get_text(URL))
    return AdapterResult(events, sorted({e.season for e in events}))
