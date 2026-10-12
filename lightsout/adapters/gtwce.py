"""GT World Challenge Europe (SRO) from gt-world-challenge-europe.com: calendar, timetables and entry lists.

Drivers move in and out of this series round by round (Verstappen's own team entered the 2026 Portimão
Endurance round), so every round's published entry list is read: the follow search finds a name there
just as it does on the WRC and WEC lists.

Pages:
  /calendar                     one card per round: dates, track, country, "Round N", Endurance or Sprint Cup
  /event/<id>/<slug>            timetable: a table per day, session / local time / GMT
  /entry-list/<year>/<slug>     car #, team, drivers 1-3, car, class
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

from ..models import RACE, AdapterResult, Entry, Event, Session, kind_from_name
from .base import get_text, slugify, tidy_caps

BASE = "https://www.gt-world-challenge-europe.com"
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def _person(name: str) -> str:
    """'Max VERSTAPPEN' -> 'Max Verstappen'; keeps 'van der', 'de' etc. as written."""
    return " ".join(w if not w.isupper() or len(w) <= 1 else w.capitalize() for w in _text(name).split())


def race_length(name: str, cup: str) -> timedelta:
    if "24 Hours" in name or "24 Hours" in cup:
        return timedelta(hours=24)
    if "Endurance" in cup:
        return timedelta(hours=3)  # the standard Endurance Cup race; Spa is the 24-hour one
    return timedelta(hours=1)      # Sprint Cup races


# ---- calendar ---------------------------------------------------------------------------------
def parse_calendar(page: str) -> list[dict]:
    """Rounds as dicts: name, country, cup, round, start/end dates, event page path. Test days are skipped."""
    out = []
    for card in re.split(r'(?=<div class="calendar__date-start">)', page)[1:]:
        nums = re.findall(r'calendar__date-number">\s*(\d+)', card)
        months = re.findall(r'calendar__date-month">\s*([A-Z]{3})', card)
        years = re.findall(r'calendar__date-year">\s*(\d{4})', card)
        name = re.search(r'calendar__race-header">(.*?)</h3>', card, re.S)
        link = re.search(r'href="(/event/(\d+)/[^"]+)"', card)
        texts = [_text(t) for t in re.findall(r'calendar__race-text">(.*?)</span>', card, re.S)]
        rnd = next((t for t in texts if t.startswith("Round")), "")
        cup = next((t for t in texts if "Cup" in t), "")
        if not (nums and months and years and name and link and rnd):
            continue  # official test days have no round number
        start = date(int(years[0]), MONTHS[months[0]], int(nums[0]))
        end = date(int(years[-1]), MONTHS[months[-1]], int(nums[-1]))
        country = re.search(r'calendar__race-subheading-text">(.*?)</span>', card, re.S)
        out.append({"name": _text(name.group(1)), "country": _text(country.group(1)) if country else "",
                    "cup": cup.replace("GT World Challenge Europe - ", ""), "round": rnd,
                    "start": start, "end": end, "path": html.unescape(link.group(1)), "id": link.group(2)})
    return out


# "19 - 20 May 2026", and across months "30 July 2026 - 2 August 2026" (the year then appears twice).
PAST_DATES = re.compile(r"\b(\d{1,2})(?: ([A-Za-z]+))?(?: \d{4})? - (\d{1,2}) ([A-Za-z]+) (\d{4})\b")


def parse_past(page: str) -> list[dict]:
    """Finished rounds: a simpler card (dates as text, track, country, round; no Cup name)."""
    out = []
    for item in re.findall(r'<li class="past-events__list-item">(.*?)</li>\s*</ul>.*?href="(/event/(\d+)/[^"]+)"', page, re.S):
        body, path, eid = item
        # The card's last field ("Round N") ends where the match above stopped, so allow end-of-text too.
        spans = [_text(x) for x in re.findall(r'past-events__piped-list-span">(.*?)</span>\s*(?:</li>|$)', body, re.S)]
        dates = next((PAST_DATES.search(x) for x in spans if PAST_DATES.search(x)), None)
        rnd = next((x for x in spans if x.startswith("Round")), "")
        strong = re.search(r"<strong>(.*?)</strong>\s*([^<]*)", body, re.S)
        if not (dates and rnd and strong):
            continue  # test days
        d1, m1, d2, m2, year = dates.groups()
        start = datetime.strptime(f"{d1} {(m1 or m2)[:3]} {year}", "%d %b %Y").date()
        end = datetime.strptime(f"{d2} {m2[:3]} {year}", "%d %b %Y").date()
        out.append({"name": _text(strong.group(1)), "country": _text(strong.group(2)), "cup": "", "round": rnd,
                    "start": start, "end": end, "path": html.unescape(path), "id": eid})
    return out


def cup_from_event(page: str) -> str:
    for cup in ("Endurance Cup", "Sprint Cup"):
        if f"GT World Challenge Europe - {cup}" in page or f"powered by AWS {cup}" in page:
            return cup
    return ""


# ---- one event: timetable and entry list -----------------------------------------------------------
def parse_timetable(page: str, year: int, cup: str, event_name: str) -> list[Session]:
    sessions = []
    for table in re.findall(r'<table class="timetable__table">(.*?)</table>', page, re.S):
        caption = _text((re.search(r"<caption[^>]*>(.*?)</caption>", table, re.S) or [None, ""])[1])
        m = re.search(r"(\d{1,2}) ([A-Za-z]+)", caption)
        if not m:
            continue
        day = datetime.strptime(f"{m.group(1)} {m.group(2)[:3]} {year}", "%d %b %Y").date()
        for row in re.findall(r"<tr>(.*?)</tr>", table, re.S):
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 3 or not re.match(r"\d{1,2}:\d{2}$", cells[2]):
                continue
            name, local, gmt = cells[0], cells[1], cells[2]
            if "Bronze" in name:
                continue  # a test only for bronze-rated (amateur) drivers: noise on everyone's schedule
            # GMT is UTC; the local time tells us whether GMT fell on the day before or after.
            lh, lm = map(int, local.split(":")) if re.match(r"\d{1,2}:\d{2}$", local) else (None, None)
            gh, gm = map(int, gmt.split(":"))
            start = datetime(day.year, day.month, day.day, gh, gm, tzinfo=timezone.utc)
            if lh is not None:
                diff = (lh * 60 + lm) - (gh * 60 + gm)
                if diff < -12 * 60:
                    start += timedelta(days=-1)  # local just after midnight, GMT still the evening before
                elif diff > 14 * 60:
                    start += timedelta(days=1)
            kind = kind_from_name(name)
            end = start + race_length(event_name, cup) if kind == RACE else None
            sessions.append(Session(key=slugify(f"{day.isoformat()} {name}"), name=name, kind=kind,
                                    start=start, end=end, day=day))
    return sessions


def parse_entry_list(page: str) -> list[dict]:
    """Rows of the entry table: number, team, drivers, car, class."""
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 5 or not _text(cells[0]).isdigit():
            continue
        drivers = [_person(c) for c in cells[2:-2] if _text(c) and _text(c).upper() != "TBA"]
        out.append({"number": _text(cells[0]), "team": tidy_caps(_text(cells[1])), "drivers": drivers,
                    "car": _text(cells[-2]), "class": _text(cells[-1]).title()})
    return out


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    page = await get_text(f"{BASE}/calendar")
    rounds = parse_past(page) + parse_calendar(page)
    events, entries, seen = [], [], set()
    for rd in rounds:
        season = rd["start"].year
        slug = rd["path"].rsplit("/", 1)[-1]
        sessions, entrants = [], None
        try:
            event_page = await get_text(BASE + rd["path"])
            rd["cup"] = rd["cup"] or cup_from_event(event_page)
            sessions = parse_timetable(event_page, season, rd["cup"], rd["name"])
        except Exception:
            pass  # the timetable isn't up yet: the round still shows, with its dates
        if not sessions:
            sessions = [Session(key="weekend", name=f"{rd['cup']} weekend", kind=RACE, day=rd["start"], end_day=rd["end"])]
        try:
            rows = parse_entry_list(await get_text(f"{BASE}/entry-list/{season}/{slug}"))
            entrants = [d for r in rows for d in r["drivers"]] or None
            for r in rows:
                for d in r["drivers"]:
                    if d not in seen:
                        seen.add(d)
                        entries.append(Entry(name=d, team=r["team"], number=r["number"]))
        except Exception:
            pass  # entry list not published yet
        name = f"{rd['name']} ({rd['cup'].replace(' Cup', '')})" if rd["cup"] else rd["name"]
        events.append(Event(key=f"{season}-{slugify(rd['name'])}", season=season, name=name, country=rd["country"],
                            venue=rd["name"], start_day=rd["start"], end_day=rd["end"],
                            url=BASE + rd["path"], sessions=sessions, entrants=entrants))
    return AdapterResult(events, sorted({e.season for e in events}), entries)
