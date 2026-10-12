"""Common data shapes every adapter produces."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# Session kinds. The calendar feed and filters key off these.
PRACTICE = "practice"
QUALIFYING = "qualifying"
SPRINT = "sprint"
RACE = "race"
RALLY_DAY = "rally_day"
EVENT = "event"  # whole multi-day event with no timetable (rally-raid, Macau before times are out)

DEFAULT_DURATION = {
    PRACTICE: timedelta(hours=1),
    QUALIFYING: timedelta(hours=1),
    SPRINT: timedelta(minutes=45),
    RACE: timedelta(hours=2),
}


def kind_from_name(name: str) -> str:
    """Best-effort session kind from a free-text session name."""
    n = name.lower()
    if "sprint" in n and ("qualif" in n or "shootout" in n):
        return QUALIFYING
    if "sprint" in n:
        return SPRINT
    if any(w in n for w in ("qualif", "hyperpole", "pole", "shootout")):
        return QUALIFYING
    if any(w in n for w in ("practice", "fp1", "fp2", "fp3", "warm", "test", "shakedown")):
        return PRACTICE
    if any(w in n for w in ("race", "feature", "final", "grand prix", "e-prix")):
        return RACE
    return PRACTICE


@dataclass
class Session:
    key: str            # stable within its event, used for calendar UIDs
    name: str
    kind: str
    start: datetime | None = None  # aware UTC datetime when the time is known
    end: datetime | None = None
    day: date | None = None        # set instead of start when only the date is known
    end_day: date | None = None    # last day (inclusive) for multi-day all-day entries
    tv: str = ""                   # network for this session when the source publishes it (NASCAR)

    @property
    def time_tba(self) -> bool:
        return self.start is None

    def normalised(self) -> "Session":
        if self.start is not None:
            self.start = self.start.astimezone(timezone.utc)
            if self.end is None:
                self.end = self.start + DEFAULT_DURATION.get(self.kind, timedelta(hours=1))
            self.end = self.end.astimezone(timezone.utc)
            if self.day is None:
                self.day = self.start.date()
        return self


@dataclass
class Event:
    key: str            # stable id within the series, e.g. "2026-yas-marina"
    season: int
    name: str
    venue: str = ""
    country: str = ""
    start_day: date | None = None
    end_day: date | None = None
    url: str = ""
    sessions: list[Session] = field(default_factory=list)
    entrants: list[str] | None = None  # published entry list (pick-your-events series); None = not out yet

    def normalised(self) -> "Event":
        for s in self.sessions:
            s.normalised()
        days = [s.day for s in self.sessions if s.day] + [s.end_day for s in self.sessions if s.end_day]
        if days:
            self.start_day = self.start_day or min(days)
            self.end_day = self.end_day or max(days)
        return self


@dataclass
class Entry:
    """A driver (or crew) and team in a series, used for the follow picker."""
    name: str
    team: str = ""
    number: str = ""


@dataclass
class AdapterResult:
    events: list[Event]
    seasons: list[int]      # seasons this result fully covers; their old rows get replaced
    entries: list[Entry] = field(default_factory=list)


@dataclass
class StandingRow:
    pos: int
    name: str
    team: str = ""
    points: float = 0
    code: str = ""      # three-letter abbreviation when the series publishes one


@dataclass
class ResultRow:
    pos: str            # text: "1", "DNF", "NC"
    name: str
    team: str = ""
    time: str = ""      # winner's time, gap to the winner, or retirement reason
    points: float = 0
    code: str = ""


@dataclass
class Standings:
    drivers: list[StandingRow] = field(default_factory=list)
    teams: list[StandingRow] = field(default_factory=list)
    last_event: str = ""
    last_session: str = ""
    last_results: list[ResultRow] = field(default_factory=list)
    season_label: str = ""
    classes: dict[str, list[StandingRow]] = field(default_factory=dict)  # support championships, e.g. WRC2
