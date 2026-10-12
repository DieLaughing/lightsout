"""Series with a handful of events a year, kept by hand in data/manual.yaml.

Each event is one of:
  * `rally_days: true`  -> one "Day N" entry per day (rallies)
  * `days: [...]`       -> one named entry per consecutive day, e.g. RX heats then finals
  * `sessions: [...]`   -> explicit sessions with ISO start times
  * none of the above   -> a single all-day entry spanning the event (timetable TBA)
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import yaml

from ..models import EVENT, RALLY_DAY, AdapterResult, Event, Session, kind_from_name
from .base import parse_dt, slugify

DATA = Path(__file__).resolve().parent.parent / "data" / "manual.yaml"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def build_event(raw: dict) -> Event:
    start, end = _day(raw["start"]), _day(raw.get("end", raw["start"]))
    sessions = []
    if raw.get("rally_days"):
        for n in range((end - start).days + 1):
            d = start + timedelta(days=n)
            sessions.append(Session(f"day{n + 1}", f"Day {n + 1} ({WEEKDAYS[d.weekday()]})", RALLY_DAY, day=d))
    elif raw.get("days"):
        for n, spec in enumerate(raw["days"]):
            name = spec["name"] if isinstance(spec, dict) else spec
            kind = spec.get("kind") if isinstance(spec, dict) else None
            sessions.append(Session(f"day{n + 1}", name, kind or kind_from_name(name), day=start + timedelta(days=n)))
    elif raw.get("sessions"):
        for s in raw["sessions"]:
            sessions.append(Session(slugify(s["name"]), s["name"], s.get("kind") or kind_from_name(s["name"]),
                                    start=parse_dt(str(s["start"]))))
    else:
        sessions.append(Session("event", "Event (timetable TBA)", EVENT, day=start, end_day=end))
    return Event(key=f"{start.year}-{slugify(raw['name'])}", season=int(raw.get("season", start.year)),
                 name=raw["name"], venue=raw.get("venue", ""), country=raw.get("country", ""),
                 url=raw.get("url", ""), start_day=start, end_day=end, sessions=sessions)


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    data = yaml.safe_load(DATA.read_text()) or {}
    events = [build_event(e) for e in data.get(series_id) or []]
    return AdapterResult(events, sorted({e.season for e in events}))
