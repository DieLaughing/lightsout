"""IndyCar live timing: polls the timing file behind indycar.com's own leaderboard.

`timingscoring.json` is the live session; between sessions it holds the last one. Events come from
changes between polls: flags, cars that stop (with IndyCar's reason, e.g. "Contact"), lead changes.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..adapters.base import get_json
from .events import EventLog

log = logging.getLogger("lightsout.live.indycar")
FEED = "https://indycar.blob.core.windows.net/racecontrol/timingscoring.json"
POLL = 10
FLAG_TEXT = {"GREEN": "Green", "YELLOW": "Caution", "RED": "Red flag", "WHITE": "White flag", "CHECKERED": "Checkered"}
TRACK = {"GREEN": "Green", "YELLOW": "Yellow", "RED": "Red flag", "WHITE": "White flag", "CHECKERED": "Checkered"}


class IndyState:
    def __init__(self):
        self.series = "indycar"
        self.raw: dict = {}
        self.changed = time.time()
        self.log = EventLog("indycar")
        self._out: set[str] = set()
        self._leader: str | None = None
        self._lap = 0
        self._flag = ""

    def _hb(self) -> dict:
        return (self.raw.get("timing_results") or {}).get("heartbeat") or {}

    def _items(self) -> list[dict]:
        return (self.raw.get("timing_results") or {}).get("Item") or []

    def _label(self) -> str:
        hb = self._hb()
        return " · ".join(x for x in (hb.get("eventName"), hb.get("SessionName")) if x)

    @staticmethod
    def _name(x: dict) -> str:
        return f"{x.get('firstName', '')} {x.get('lastName', '')}".strip()

    def apply(self, data: dict) -> None:
        self.raw = data or {}
        self.changed = time.time()
        try:
            self._events()
        except Exception:
            log.exception("IndyCar event extraction failed")

    def _events(self) -> None:
        hb, items = self._hb(), self._items()
        race = hb.get("SessionType") == "R"
        session = f"{hb.get('EventID')}:{hb.get('EventSessionID')}"
        label = self._label()
        by_no = {str(x.get("no")): x for x in items}
        leader = next((str(x.get("no")) for x in items if str(x.get("rank")) == "1"), None)
        out = {str(x.get("no")) for x in items if x.get("status") not in (None, "", "Active")}
        flag = (hb.get("currentFlag") or "").upper()
        lap = int(hb.get("lapNumber") or 0) if str(hb.get("lapNumber") or "").isdigit() else 0
        if not self.log.primed:
            self._out, self._leader, self._lap, self._flag = out, leader, lap, flag
            self.log.primed = bool(items)
            return
        if not race:
            self._flag = flag
            return
        if flag != self._flag and flag:
            if flag == "YELLOW":
                self.log.emit(f"{session}:yellow:{lap}", "caution", f"Caution, lap {lap}", lap=lap, session=label)
            elif flag == "RED":
                self.log.emit(f"{session}:red:{lap}", "red_flag", f"Red flag, lap {lap}", lap=lap, session=label)
            elif flag == "GREEN" and self._flag in ("YELLOW", "RED"):
                self.log.emit(f"{session}:green:{lap}", "restart", f"Green flag, lap {lap}", lap=lap, session=label)
            elif flag == "CHECKERED":
                top = sorted(items, key=lambda x: int(x.get("rank") or 99))[:3]
                podium = ", ".join(f"P{i + 1} {self._name(x)}" for i, x in enumerate(top))
                self.log.emit(f"{session}:result", "result", f"{label} result: {podium}", lap=lap,
                              numbers=[str(x.get("no")) for x in top], names=[self._name(x) for x in top], session=label)
            self._flag = flag
        if flag != "CHECKERED":
            for n in sorted(out - self._out):
                x = by_no.get(n, {})
                why = f" ({x['comment']})" if x.get("comment") else ""
                self.log.emit(f"{session}:out:{n}", "retired", f"{self._name(x)} is out of the race{why}", lap=lap,
                              numbers=[n], names=[self._name(x)], session=label)
        self._out |= out
        if lap > self._lap:
            if leader and leader != self._leader and flag == "GREEN":
                self.log.emit(f"{session}:lead:{leader}:{lap}", "lead", f"{self._name(by_no.get(leader, {}))} takes the lead",
                              lap=lap, numbers=[leader], names=[self._name(by_no.get(leader, {}))], session=label)
            self._leader, self._lap = leader or self._leader, lap

    def snapshot(self) -> dict:
        hb, items = self._hb(), self._items()
        race = hb.get("SessionType") == "R"
        rows = []
        for x in items:
            rank = x.get("rank")
            rows.append({
                "pos": int(rank) if str(rank).isdigit() else 99, "number": str(x.get("no", "")),
                "code": f"#{x.get('no', '')}", "name": self._name(x), "team": x.get("team", ""), "colour": "",
                "gap": "" if str(rank) == "1" else (x.get("diff") or ""),
                "interval": "" if str(rank) == "1" else (x.get("gap") or ""),
                "last": x.get("lastLapTime") or "", "best": x.get("bestLapTime") or "",
                "laps": x.get("laps") or "", "pits": x.get("pitStops") or 0, "tyre": "",
                "status": "OUT" if x.get("status") not in (None, "", "Active") else "",
            })
        rows.sort(key=lambda r: r["pos"])
        flag = (hb.get("currentFlag") or "").upper()
        return {
            "series": "indycar", "kind": "race" if race else "timed",
            "event": hb.get("eventName", ""), "session": hb.get("SessionName", ""),
            "status": hb.get("SessionStatus", ""), "track": TRACK.get(flag, ""),
            "lap": hb.get("lapNumber"), "total_laps": hb.get("totalLaps"), "clock": "",
            "rows": rows, "messages": [], "updated": self.changed, "venue": hb.get("trackName", ""),
        }


async def run(state: IndyState, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            state.apply(await get_json(FEED))
        except Exception as exc:
            log.warning("IndyCar live poll failed: %r", exc)
        try:
            await asyncio.wait_for(stop.wait(), POLL)
        except asyncio.TimeoutError:
            pass
