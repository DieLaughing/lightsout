"""NASCAR live timing: polls the public live feed NASCAR's own leaderboard uses.

One feed covers whichever national series is on track; each series' state only takes it
when the feed's series_id is theirs.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

from ..adapters.base import get_json
from ..adapters.nascar import SERIES_NO, _name
from .events import EventLog

log = logging.getLogger("lightsout.live.nascar")
FEED = "https://cf.nascar.com/cacher/live/live-feed.json"
FLAGS_FEED = "https://cf.nascar.com/cacher/live/live-flag-data.json"
NOTES_FEED = "https://cf.nascar.com/cacher/{year}/{series}/{race}/lap-notes.json"
POLL = 5
EXTRA_POLL = 15   # flag history and lap notes
# Lap notes are play-by-play; only penalties are Wire-worthy (cautions come from the flag data, with the reason).
PENALTY = re.compile(r"penalt|pass[- ]through|stop[- ]and[- ]go|black flag|speeding|commitment line|too many crew|"
                     r"uncontrolled tire|tire violation|crew member over", re.I)
FLAGS = {1: "Green", 2: "Caution", 3: "Red flag", 4: "White flag", 8: "Warm up", 9: "Checkered"}
MAKES = {"Chv": "Chevrolet", "Frd": "Ford", "Tyt": "Toyota"}


class NascarState:
    def __init__(self, series: str):
        self.series = series
        self.raw: dict = {}
        self.changed = time.time()
        self.log = EventLog(series)
        self._out: set[str] = set()
        self._leader: str | None = None
        self._lap = 0
        self._stage = 0
        self._flag = None

    def apply(self, feed: dict) -> None:
        if feed.get("series_id") != SERIES_NO[self.series]:
            return
        self.raw = feed
        self.changed = time.time()
        try:
            self._events()
        except Exception:
            log.exception("NASCAR event extraction failed")

    # ---- events ------------------------------------------------------------------------------
    def _label(self) -> str:
        f = self.raw
        return " · ".join(x for x in (f.get("run_name"), {1: "Practice", 2: "Qualifying", 3: "Race"}.get(f.get("run_type"))) if x)

    def _car(self, number: str) -> str:
        number = str(number).strip().lstrip("#")
        v = next((v for v in self.raw.get("vehicles") or [] if str(v.get("vehicle_number")) == number), None)
        return f"#{number} {_name((v.get('driver') or {}).get('full_name', ''))}" if v else f"#{number}"

    def _driver(self, number: str) -> str:
        number = str(number).strip().lstrip("#")
        v = next((v for v in self.raw.get("vehicles") or [] if str(v.get("vehicle_number")) == number), None)
        return _name((v.get("driver") or {}).get("full_name", "")) if v else ""

    def _events(self) -> None:
        f = self.raw
        if f.get("run_type") != 3:
            return  # practice and qualifying: nothing Wire-worthy happens
        key = f"{f.get('race_id')}"
        label = self._label()
        cars = f.get("vehicles") or []
        leader = next((str(v.get("vehicle_number")) for v in cars if v.get("running_position") == 1), None)
        out = {str(v.get("vehicle_number")) for v in cars if v.get("status") not in (None, 1)}
        lap, stage, flag = f.get("lap_number") or 0, (f.get("stage") or {}).get("stage_num") or 0, f.get("flag_state")
        if not self.log.primed:
            self._out, self._leader, self._lap, self._stage, self._flag = out, leader, lap, stage, flag
            self.log.primed = bool(cars)
            return
        for n in sorted(out - self._out):
            self.log.emit(f"{key}:out:{n}", "retired", f"{self._car(n)} is out of the race", lap=lap,
                          numbers=[n], names=[self._driver(n)], session=label)
        self._out |= out
        if lap > self._lap:
            if leader and leader != self._leader and flag == 1:
                self.log.emit(f"{key}:lead:{leader}:{lap}", "lead", f"{self._car(leader)} takes the lead", lap=lap,
                              numbers=[leader], names=[self._driver(leader)], session=label)
            self._leader, self._lap = leader or self._leader, lap
        if stage > self._stage and self._stage:
            self.log.emit(f"{key}:stage:{self._stage}", "stage", f"Stage {self._stage} goes to {self._car(self._leader or '')}",
                          lap=lap, numbers=[self._leader] if self._leader else [], names=[self._driver(self._leader or "")],
                          session=label)
        self._stage = max(stage, self._stage)
        if flag == 9 and self._flag != 9:
            top = [str(v.get("vehicle_number")) for v in sorted(cars, key=lambda v: v.get("running_position") or 99)[:3]]
            podium = ", ".join(f"P{i + 1} {self._car(n)}" for i, n in enumerate(top))
            self.log.emit(f"{key}:result", "result", f"{label} result: {podium}", lap=lap, numbers=top,
                          names=[self._driver(n) for n in top], session=label)
        self._flag = flag

    def apply_flags(self, flags: list[dict]) -> None:
        """Flag history: every caution with its reason and the free-pass car."""
        label, key = self._label(), f"{self.raw.get('race_id')}"
        for x in flags or []:
            state, lap = x.get("flag_state"), x.get("lap_number")
            when = _utc(x.get("time_of_day_os"))
            if state == 2:
                reason = (x.get("comment") or "").strip()
                free = (x.get("beneficiary") or "").strip()
                text = f"Caution, lap {lap}" + (f": {reason}" if reason else "") + (f" (free pass #{free})" if free else "")
                # "#54, 6 incident turn 4": the cars are the list at the start, not the turn number.
                lead = re.match(r"\s*#\s*((?:\d{1,3}\s*(?:,|&|and)?\s*#?\s*)+)", reason)
                nums = re.findall(r"\d{1,3}", lead.group(1)) if lead else []
                self.log.emit(f"{key}:caution:{lap}:{when}", "caution", text, lap=lap, numbers=nums,
                              names=[d for d in (self._driver(n) for n in nums) if d], time=when, session=label)
            elif state == 3:
                self.log.emit(f"{key}:red:{lap}:{when}", "red_flag", f"Red flag, lap {lap}", lap=lap, time=when, session=label)
            elif state == 1 and lap:
                self.log.emit(f"{key}:green:{lap}:{when}", "restart", f"Green flag, lap {lap}", lap=lap, time=when, session=label)

    def apply_notes(self, notes: dict) -> None:
        """Lap-by-lap play-by-play. Kept for the Race story; penalties also reach the Wire."""
        label, key = self._label(), f"{self.raw.get('race_id')}"
        for lap, items in sorted(((notes or {}).get("laps") or {}).items(), key=lambda kv: int(kv[0])):
            for n in items or []:
                text = (n.get("Note") or "").strip()
                if not text:
                    continue
                nums = list(dict.fromkeys(re.findall(r"#(\d{1,3})\b", text)))
                kind = "penalty" if PENALTY.search(text) else "note"
                self.log.emit(f"{key}:note:{n.get('NoteID')}", kind, text, lap=int(lap), numbers=nums,
                              names=[d for d in (self._driver(x) for x in nums) if d], session=label)

    def snapshot(self) -> dict:
        f = self.raw
        cars = sorted(f.get("vehicles") or [], key=lambda v: v.get("running_position") or 99)
        leader_laps = cars[0].get("laps_completed", 0) if cars else 0
        race = f.get("run_type") == 3
        rows = []
        for v in cars:
            down = leader_laps - (v.get("laps_completed") or 0)
            delta = v.get("delta") or 0
            gap = (f"-{down} lap{'s' if down > 1 else ''}" if race and down > 0 else
                   f"+{delta:.3f}" if delta and delta > 0 else "")
            rows.append({
                "pos": v.get("running_position") or 99, "number": v.get("vehicle_number", ""),
                "code": f"#{v.get('vehicle_number', '')}", "name": _name((v.get("driver") or {}).get("full_name", "")),
                "team": "", "colour": "",
                "gap": gap, "interval": "",
                "last": f"{v['last_lap_time']:.3f}" if v.get("last_lap_time") else "",
                "best": f"{v['best_lap_time']:.3f}" if v.get("best_lap_time") else "",
                # Outside races "pit stops" are trips down pit road (every qualifying run), so they aren't shown.
                "laps": v.get("laps_completed") or "", "pits": len(v.get("pit_stops") or []) if race else 0,
                "tyre": "", "status": "" if v.get("status") in (None, 1) else "OUT",
                "make": MAKES.get(v.get("vehicle_manufacturer", ""), v.get("vehicle_manufacturer", "")),
            })
        return {
            "series": self.series, "kind": "race" if race else "timed",
            "event": f.get("run_name", ""), "session": {1: "Practice", 2: "Qualifying", 3: "Race"}.get(f.get("run_type"), ""),
            "status": "", "track": FLAGS.get(f.get("flag_state"), ""),
            # Practice and qualifying carry lap 1 of 999 (a placeholder): laps only mean something in a race.
            "lap": f.get("lap_number") if race else None, "total_laps": f.get("laps_in_race") if race else None, "clock": "",
            "rows": rows, "messages": [], "updated": self.changed,
            "venue": f.get("track_name", ""),
        }


def make_state(series: str):
    return lambda: NascarState(series)


def _utc(value: str | None) -> str | None:
    """'2026-10-11T15:37:34.797-04:00' -> '2026-10-11T19:37:34Z'."""
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


async def run(state: NascarState, stop: asyncio.Event) -> None:
    last_extra = 0.0
    while not stop.is_set():
        try:
            state.apply(await get_json(FEED))
            f = state.raw
            if f and f.get("run_type") == 3 and time.time() - last_extra >= EXTRA_POLL:
                last_extra = time.time()
                year = datetime.now(timezone.utc).year
                state.apply_flags(await get_json(FLAGS_FEED))
                state.apply_notes(await get_json(NOTES_FEED.format(year=year, series=f.get("series_id"), race=f.get("race_id"))))
        except Exception as exc:
            log.warning("NASCAR live poll failed: %r", exc)
        try:
            await asyncio.wait_for(stop.wait(), POLL)
        except asyncio.TimeoutError:
            pass
