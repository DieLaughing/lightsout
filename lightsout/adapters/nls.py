"""Nürburgring Langstrecken-Serie (NLS) from its own WordPress site (the /wp-json API serves clean text).

  - this year's calendar page:  "14.03.2026 NLS1: 71. ADAC Westfalenfahrt (4h)", "18.-19.04.2026 ADAC 24h Qualifiers (2x4h)"
  - next year's dates come from the news post that announces them (no calendar page yet), e.g.
    "NLS 1: 20 March 2027", "NLS 4: 7 August 2027 (6-hour race)", "NLS 5 & 6: 11/12 September 2027 (double-header)"

The series publishes no timetable or entry list in a form we can read (real entry lists only appear in race
preview articles), so races are day-long entries without start times, and drivers are spotted through the news
(the series' own posts are in the Race Wire).
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime

from ..models import RACE, AdapterResult, Event, Session
from .base import get_json

WP = "https://www.nuerburgring-langstrecken-serie.de/wp-json/wp/v2"
SITE = "https://www.nuerburgring-langstrecken-serie.de/language/en/calendar-nurburgring-langstrecken-serie-{year}/"
VENUE = "Nürburgring Nordschleife"


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def _event(year: int, label: str, title: str, days: list[date], hours: str, url: str) -> Event:
    number = re.sub(r"\s+", "", label)  # "NLS 1" -> "NLS1"; "" for the 24h Qualifiers
    length = f"{hours}h race" if hours else "Race"
    sessions = [Session(key=f"race{i + 1}", name=length if len(days) == 1 else f"{length} {i + 1}", kind=RACE, day=d)
                for i, d in enumerate(days)]
    name = f"{number} · {title}" if number and title else (number or title)
    key = f"{year}-{re.sub(r'[^a-z0-9]+', '-', (number or title).lower()).strip('-')}"
    return Event(key=key, season=year, name=name, venue=VENUE, country="Germany", url=url, sessions=sessions)


# ---- this year's calendar page --------------------------------------------------------------------
DATE = re.compile(r"(\d{1,2})\.(?:-(\d{1,2})\.)?(\d{2})\.(\d{4})")


def parse_calendar(text: str, url: str) -> list[Event]:
    """One race per date: split the text at each date, so a line without a "(4h)" suffix can't swallow the next."""
    events = []
    marks = list(DATE.finditer(text))
    for i, m in enumerate(marks):
        d1, d2, month, year = m.groups()
        line = text[m.end():marks[i + 1].start() if i + 1 < len(marks) else m.end() + 120].strip()
        line = line.split(" Facts")[0]
        label = re.match(r"(NLS\s?\d+):\s*", line)
        title = line[label.end():] if label else line
        # Race length: "(4h)" / "(2x4h)" wins over a bare "6h" in the name ("24h Qualifiers" are 4h races).
        hours = re.search(r"\((?:\d+x)?(\d+)h\)", title) or re.search(r"\b(\d+)h\b", title)
        hours = hours.group(1) if hours else "4"
        title = re.sub(r"\s*\([^)]*\)\s*$", "", title)        # "(4h)"
        title = re.sub(r"^\d+\.\s*", "", title).strip()       # the "71." edition number
        if not title and not label:
            continue
        y, mo = int(year), int(month)
        days = [date(y, mo, int(d1) + k) for k in range((int(d2) - int(d1) + 1) if d2 else 1)]
        events.append(_event(y, label.group(1) if label else "", title, days, hours, url))
    return events


# ---- next year's dates, from the announcement post ----------------------------------------------------
POST_LINE = re.compile(r"(NLS\s?\d+(?:\s*&(?:amp;)?\s*\d+)?):\s*(\d{1,2})(?:/(\d{1,2}))?\s+([A-Za-z]+)\s+(\d{4})(?:\s*\(([^)]*)\))?")


def parse_announcement(text: str, url: str) -> list[Event]:
    events = []
    for label, d1, d2, month, year, note in POST_LINE.findall(text):
        y = int(year)
        m = datetime.strptime(month[:3], "%b").month
        hours = (re.search(r"(\d+)-hour", note or "") or [None, "4"])[1]
        nums = [int(n) for n in re.findall(r"\d+", label)]
        days = [date(y, m, int(d1))] + ([date(y, m, int(d2))] if d2 else [])
        if len(nums) == 2 and len(days) == 2:  # "NLS 5 & 6: 11/12 September": one race each day
            events += [_event(y, f"NLS{n}", "", [d], hours, url) for n, d in zip(nums, days)]
        else:
            events.append(_event(y, f"NLS{nums[0]}", "", days, hours, url))
    return events


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events, covered = [], []
    for year in seasons:
        page = await get_json(f"{WP}/pages?slug=calendar-nurburgring-langstrecken-serie-{year}")
        if page:
            evs = parse_calendar(_text(page[0]["content"]["rendered"]), SITE.format(year=year))
        else:  # no calendar page yet: the dates as first announced in the news
            posts = await get_json(f"{WP}/posts?search={year}%20calendar&per_page=5")
            evs = []
            for p in posts:
                evs = [e for e in parse_announcement(_text(p["content"]["rendered"]), p["link"]) if e.season == year]
                if evs:
                    break
        if evs:
            events += evs
            covered.append(year)
    return AdapterResult(events, covered)
