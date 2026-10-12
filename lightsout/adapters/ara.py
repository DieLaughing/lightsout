"""ARA National Championship from americanrallyassociation.org/<year>-ara-schedule.

Each National round on that page is four lines: name, "(National/<region>)", dates, location.
New seasons are picked up when ARA publishes the page (also found via homepage links).
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta

from ..models import RALLY_DAY, AdapterResult, Event, Session
from .base import get_text, slugify

SITE = "https://www.americanrallyassociation.org"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# "February 6-7, 2026", "July 25, 2026", "July 30-August 1, 2026"
DATES = re.compile(r"([A-Za-z]+)\s+(\d{1,2})(?:\s*[-–]\s*(?:([A-Za-z]+)\s+)?(\d{1,2}))?,?\s*(\d{4})")


def _lines(page: str) -> list[str]:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    page = re.sub(r"(?i)<br\s*/?>|</(p|h[1-6]|div|li)>", "\n", page)
    text = html.unescape(re.sub(r"<[^>]+>", "", page)).replace("​", "")
    return [line.strip() for line in text.split("\n") if line.strip()]


def _date(month: str, day: str, year: str) -> date:
    return datetime.strptime(f"{month[:3]} {day} {year}", "%b %d %Y").date()


def parse_schedule(page: str, season: int) -> list[Event]:
    lines = _lines(page)
    events = []
    for i, line in enumerate(lines):
        if not re.match(r"^\(National\b", line) or i == 0 or i + 1 >= len(lines):
            continue
        m = DATES.search(lines[i + 1])
        if not m:
            continue
        m1, d1, m2, d2, year = m.groups()
        first = _date(m1, d1, year)
        last = _date(m2 or m1, d2 or d1, year)
        name = lines[i - 1]
        sessions = [Session(f"day{n + 1}", f"Day {n + 1} ({WEEKDAYS[(first + timedelta(days=n)).weekday()]})",
                            RALLY_DAY, day=first + timedelta(days=n))
                    for n in range((last - first).days + 1)]
        events.append(Event(key=f"{season}-{slugify(name)}", season=season, name=name,
                            venue=lines[i + 2] if i + 2 < len(lines) else "", country="USA",
                            url=f"{SITE}/{season}-ara-schedule", sessions=sessions))
    return events


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    # ARA links the current schedule page from its homepage; also try the usual address per season.
    try:
        linked = {int(y) for y in re.findall(r"/(\d{4})-ara-schedule", await get_text(SITE))}
    except Exception:
        linked = set()
    events, covered = [], []
    for season in sorted(set(seasons) | (linked & set(range(min(seasons), max(seasons) + 1)))):
        try:
            page = await get_text(f"{SITE}/{season}-ara-schedule")
        except Exception:
            continue  # not published yet
        season_events = parse_schedule(page, season)
        if season_events:
            events += season_events
            covered.append(season)
    return AdapterResult(events, covered)
