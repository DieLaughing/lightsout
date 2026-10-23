"""F2, F3 and F1 Academy live timing: the ASP.NET SignalR hub behind each series' own timing page.

No login. `GetData2` returns the full current state, `JoinFeeds` subscribes to updates.
Driver lines are keyed by racing number; updates carry only the fields that changed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request

import websockets

from ..adapters.f1academy import WILDCARD
from .events import EventLog, car_numbers, classify, short_hash, tidy
from .f1 import merge

log = logging.getLogger("lightsout.live.fia")

SOURCES = {
    "f2": ("https://ltss.fiaformula2.com/streaming", "F2"),
    "f3": ("https://ltss.fiaformula2.com/streaming", "F3"),
    "f1a": ("https://f2f3-prod-livetiming.azurewebsites.net/streaming", "F1 Academy"),
}
FEEDS = ["data", "statsfeed", "weatherfeed", "sessionfeed", "trackfeed", "commentaryfeed", "timefeed", "racedetailsfeed"]
TRACK = {"1": "Green", "2": "Yellow", "4": "Safety car", "5": "Red flag", "6": "Virtual safety car", "7": "VSC ending"}


def _name(d: dict) -> str:
    """'ALEXIA DANIELSSON (WCD)' -> 'Alexia Danielsson': the wild-card mark is a flag on the row, not part of the name."""
    return " ".join(WILDCARD.sub("", d.get("FullName") or "").split()).title()


def _v(x) -> str:
    return (x or {}).get("Value", "") if isinstance(x, dict) else (x or "")


class FiaState:
    def __init__(self, series: str):
        self.series = series
        self.lines: dict[str, dict] = {}
        self.session = ""
        self.status = ""
        self.track = ""
        self.clock = ""
        self.details: dict = {}
        self.comments: list[str] = []
        self.raw = self.lines  # non-empty once data arrives (the hub checks this)
        self.changed = time.time()
        self.log = EventLog(series)
        self._retired: set[str] = set()

    def _label(self) -> str:
        return " · ".join(x for x in (self.details.get("Race"), self.details.get("Session") or self.session) if x)

    def _driver(self, num: str) -> str:
        d = (self.lines.get(str(num)) or {}).get("driver") or {}
        return _name(d) or f"#{num}"

    def _check_retirements(self) -> None:
        out = {n for n, l in self.lines.items() if (l.get("status") or {}).get("Retired") or (l.get("status") or {}).get("Stopped")}
        if not self.log.primed:
            if self.lines:
                self._retired, self.log.primed = out, True
            return
        if self.session != "Race":
            return
        label = self._label()
        for n in sorted(out - self._retired):
            self.log.emit(f"{label}:retired:{n}", "retired", f"{self._driver(n)} is out of the race",
                          numbers=[n], names=[self._driver(n)], session=label)
        self._retired |= out

    def apply_lines(self, feed: dict, lines) -> None:
        if feed.get("UpdateType") == "new":
            self.lines.clear()
        if feed.get("Session"):
            self.session = feed["Session"]
        for line in (lines.values() if isinstance(lines, dict) else lines or []):
            num = ((line or {}).get("driver") or {}).get("RacingNumber")
            if num:
                self.lines[num] = merge(self.lines.get(num, {}), line)
        self.changed = time.time()
        self._check_retirements()

    def handle(self, method: str, args: list) -> None:
        m = method.lower()
        if m == "datafeed" and len(args) >= 3 and isinstance(args[2], dict):
            self.apply_lines(args[2], args[2].get("lines"))
        elif m == "sessionfeed":
            self.status = _v(args[1])
        elif m == "trackfeed":
            self.track = TRACK.get(str((args[1] or {}).get("Value")), (args[1] or {}).get("Message", ""))
        elif m == "timefeed" and len(args) >= 3:
            self.clock = args[2]
        elif m == "racedetailsfeed":
            self.details = args[1] or {}
        elif m == "comment" and args:
            text = args[0].get("Text") if isinstance(args[0], dict) else str(args[0])
            if text:
                self.comments = ([text] + self.comments)[:6]
                kind = classify(text)
                if kind:
                    nums = car_numbers(text)
                    label = self._label()
                    self.log.emit(f"{label}:rc:{short_hash(text)}", kind, tidy(text), numbers=nums,
                                  names=[self._driver(n) for n in nums], session=label)
        self.changed = time.time()

    def load(self, r: dict) -> None:
        """The full state returned by GetData2."""
        if r.get("data"):
            st, feed, lines = r["data"][:3]
            self.apply_lines(feed, lines)
        for key, method in (("sessionfeed", "sessionfeed"), ("trackfeed", "trackfeed"),
                            ("racedetailsfeed", "racedetailsfeed")):
            if r.get(key):
                self.handle(method, r[key])
        if r.get("timefeed"):
            self.handle("timefeed", r["timefeed"])

    def snapshot(self) -> dict:
        race = self.session == "Race"
        # After the flag every car sits in the pits; "PIT" then says nothing and hid each car's gap.
        over = self.status.lower().startswith(("final", "finish", "end", "complete"))
        rows = []
        for num, l in self.lines.items():
            d = l.get("driver") or {}
            st = l.get("status") or {}
            pos = _v(l.get("position"))
            rows.append({
                "pos": int(pos) if str(pos).isdigit() else 99, "number": num,
                "code": d.get("TLA") or num, "name": _name(d), "team": "", "colour": "",
                "wildcard": bool(WILDCARD.search(d.get("FullName") or "")),
                "gap": _v(l.get("gap")), "interval": _v(l.get("interval")),
                "last": _v(l.get("last")), "best": _v(l.get("best")),
                "laps": _v(l.get("laps")), "pits": int(_v(l.get("pits")) or 0) if str(_v(l.get("pits"))).isdigit() else 0,
                "tyre": "",
                "status": "OUT" if st.get("Retired") or st.get("Stopped") else "PIT" if st.get("InPit") and not over else "",
            })
        rows.sort(key=lambda r: r["pos"])
        laps = [int(r["laps"]) for r in rows if str(r["laps"]).isdigit()]
        return {
            "series": self.series,
            "event": " · ".join(x for x in (self.details.get("Race"), self.details.get("Round")) if x),
            "session": self.details.get("Session") or self.session,
            "kind": "race" if race else "timed",
            "status": self.status, "track": self.track,
            "lap": max(laps) if laps and race else None, "total_laps": None,
            "clock": "" if self.clock in ("00:00:00", "") else self.clock,
            "rows": rows,
            "messages": [{"lap": None, "text": c} for c in self.comments],
            "updated": self.changed,
        }


def make_state(series: str):
    return lambda: FiaState(series)


async def run(state: FiaState, stop: asyncio.Event) -> None:
    base, name = SOURCES[state.series]
    cd = urllib.parse.quote(json.dumps([{"name": "Streaming"}]))
    delay = 2
    while not stop.is_set():
        try:
            neg = await asyncio.to_thread(lambda: json.load(urllib.request.urlopen(
                f"{base}/negotiate?clientProtocol=1.5&connectionData={cd}", timeout=15)))
            qs = (f"transport=webSockets&clientProtocol=1.5&connectionToken="
                  f"{urllib.parse.quote(neg['ConnectionToken'])}&connectionData={cd}")
            async with websockets.connect(base.replace("https", "wss") + f"/connect?{qs}", max_size=None,
                                          open_timeout=20) as ws:
                await asyncio.to_thread(lambda: urllib.request.urlopen(f"{base}/start?{qs}", timeout=15).read())
                await ws.send(json.dumps({"H": "Streaming", "M": "JoinFeeds", "I": 0,
                                          "A": [name, ["data", "weather", "status", "time", "commentary", "racedetails"]]}))
                await ws.send(json.dumps({"H": "Streaming", "M": "GetData2", "A": [name, FEEDS], "I": 1}))
                log.info("connected to %s live timing", name)
                delay = 2
                while not stop.is_set():
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), 60))
                    except asyncio.TimeoutError:
                        raise ConnectionError("no data or keep-alive for 60s")
                    if msg.get("I") == "1" and isinstance(msg.get("R"), dict):
                        state.load(msg["R"])
                    for m in msg.get("M") or []:
                        if m.get("H", "").lower() == "streaming":
                            state.handle(m.get("M", ""), m.get("A") or [])
        except Exception as exc:
            if stop.is_set():
                break
            log.warning("%s live timing connection lost (%r); retrying in %ss", name, exc, delay)
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 60)


async def fetch_once(series: str, timeout: float = 20) -> FiaState:
    """Connect just long enough to receive the full current state (used for last-race results)."""
    state, stop = FiaState(series), asyncio.Event()
    task = asyncio.create_task(run(state, stop))
    try:
        for _ in range(int(timeout * 4)):
            if state.lines:
                break
            await asyncio.sleep(0.25)
    finally:
        stop.set()
        task.cancel()
    return state
