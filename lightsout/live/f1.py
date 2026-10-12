"""F1 live timing from F1's own SignalR Core feed (livetiming.formula1.com), no login needed
for the core timing topics. The same topics are archived after each session as
`<topic>.jsonStream` files, which `replay()` plays back for testing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

from .events import EventLog, car_numbers, classify, short_hash, tidy

log = logging.getLogger("lightsout.live.f1")

BASE = "https://livetiming.formula1.com"
RS = "\x1e"  # SignalR record separator
TOPICS = ["Heartbeat", "SessionInfo", "SessionStatus", "DriverList", "TimingData", "TimingAppData",
          "TrackStatus", "LapCount", "RaceControlMessages", "ExtrapolatedClock"]

TRACK = {"1": "Green", "2": "Yellow", "4": "Safety car", "5": "Red flag", "6": "Virtual safety car",
         "7": "VSC ending"}


def merge(target, update):
    """Apply a feed delta. Lists arrive as dicts keyed by index ("3": {...}) after the first message."""
    if isinstance(update, dict) and isinstance(target, list):
        for k, v in update.items():
            i = int(k)
            if i < len(target):
                target[i] = merge(target[i], v) if isinstance(v, dict) else v
            else:
                target.append(v)
        return target
    if isinstance(update, dict) and isinstance(target, dict):
        for k, v in update.items():
            target[k] = merge(target[k], v) if k in target and isinstance(v, dict) else v
        return target
    return update


def _value(x) -> str:
    return (x or {}).get("Value", "") if isinstance(x, dict) else (x or "")


class F1State:
    """Raw topic state plus a normalised snapshot for the web app."""

    def __init__(self):
        self.raw: dict = {}
        self.changed = time.time()
        self.log = EventLog("f1")
        self._retired: set[str] = set()
        self._pits: dict[str, int] = {}
        self._leader: str | None = None   # last leader we announced
        self._lap = 0
        self._status = ""

    def apply(self, topic: str, data) -> None:
        if topic.endswith(".z"):
            return  # compressed car data / positions: not used
        self.raw[topic] = merge(self.raw.get(topic, {} if isinstance(data, dict) else []), data)
        self.changed = time.time()
        try:
            self._events(topic)
        except Exception:  # events are a bonus; never let them break timing
            log.exception("F1 event extraction failed")

    # ---- events ---------------------------------------------------------------------------
    def _driver(self, num: str) -> str:
        d = (self.raw.get("DriverList") or {}).get(str(num)) or {}
        return (d.get("FullName") or "").title() or d.get("Tla") or f"#{num}"

    def _session(self) -> tuple[str, str, bool]:
        info = self.raw.get("SessionInfo", {})
        label = f"{info.get('Meeting', {}).get('Name', '')} · {info.get('Name', '')}".strip(" ·")
        race = info.get("Type", "") in ("Race", "Sprint")
        return str(info.get("Key") or info.get("Path") or label), label, race

    def _events(self, topic: str) -> None:
        skey, label, race = self._session()
        if topic == "RaceControlMessages":
            msgs = self.raw["RaceControlMessages"].get("Messages", [])
            for m in (msgs.values() if isinstance(msgs, dict) else msgs):
                text = m.get("Message", "")
                kind = classify(text, m.get("Category", ""), m.get("Flag", ""))
                if not kind:
                    continue
                nums = car_numbers(text) + ([str(m["RacingNumber"])] if m.get("RacingNumber") else [])
                nums = list(dict.fromkeys(nums))
                utc = m.get("Utc", "")
                self.log.emit(f"{skey}:rc:{utc}:{short_hash(text)}", kind, tidy(text), lap=m.get("Lap"),
                              numbers=nums, names=[self._driver(n) for n in nums],
                              time=(utc[:19] + "Z") if utc else None, session=label)
            return
        if topic not in ("TimingData", "SessionStatus", "LapCount"):
            return
        lines = self.raw.get("TimingData", {}).get("Lines", {})
        status = self.raw.get("SessionStatus", {}).get("Status", "")
        leader = next((n for n, t in lines.items() if str(t.get("Position")) == "1"), None)
        lap = self.raw.get("LapCount", {}).get("CurrentLap")
        if not self.log.primed:
            # First full picture after connecting: remember it, don't report it.
            if lines and self.raw.get("DriverList"):
                self._retired = {n for n, t in lines.items() if t.get("Retired") or t.get("Stopped")}
                self._pits = {n: int(t.get("NumberOfPitStops") or 0) for n, t in lines.items()}
                self._leader, self._status, self.log.primed = leader, status, True
                self._lap = int(lap or 0)
            return
        if race and status != self._status:
            if status == "Started" and self._status in ("", "Inactive"):
                self.log.emit(f"{skey}:start", "start", f"{label}: lights out", session=label)
            if status in ("Finished", "Finalised", "Ends"):
                top = sorted(((int(t.get("Position") or 99), n) for n, t in lines.items()))[:3]
                podium = ", ".join(f"P{p} {self._driver(n)}" for p, n in top if p < 99)
                self.log.emit(f"{skey}:result", "result", f"{label} result: {podium}",
                              numbers=[n for _, n in top], names=[self._driver(n) for _, n in top], session=label)
        self._status = status
        if not race or status in ("Finished", "Finalised", "Ends"):
            return  # cars parking after the flag aren't retirements
        for n, t in lines.items():
            if (t.get("Retired") or t.get("Stopped")) and n not in self._retired:
                self._retired.add(n)
                self.log.emit(f"{skey}:retired:{n}", "retired", f"{self._driver(n)} is out of the race",
                              lap=lap, numbers=[n], names=[self._driver(n)], session=label)
            stops = int(t.get("NumberOfPitStops") or 0)
            if stops > self._pits.get(n, 0):
                self._pits[n] = stops
                self.log.emit(f"{skey}:pit:{n}:{stops}", "pit", f"{self._driver(n)} pits (stop {stops})",
                              lap=lap, numbers=[n], names=[self._driver(n)], session=label)
        # Judge the lead once per lap, at the line: pit stops shuffle position 1 for a few seconds.
        if lap and int(lap) > self._lap:
            self._lap = int(lap)
            if leader and leader != self._leader and status == "Started":
                self.log.emit(f"{skey}:lead:{leader}:{lap}", "lead", f"{self._driver(leader)} takes the lead",
                              lap=lap, numbers=[leader], names=[self._driver(leader)], session=label)
            self._leader = leader or self._leader

    def snapshot(self) -> dict:
        info = self.raw.get("SessionInfo", {})
        drivers = self.raw.get("DriverList", {})
        timing = self.raw.get("TimingData", {}).get("Lines", {})
        app = self.raw.get("TimingAppData", {}).get("Lines", {})
        session_type = info.get("Type", "")
        race = session_type in ("Race", "Sprint") or "Race" in info.get("Name", "")
        rows = []
        for num, t in timing.items():
            d = drivers.get(num, {}) if isinstance(drivers, dict) else {}
            stints = (app.get(num) or {}).get("Stints") or []
            stints = list(stints.values()) if isinstance(stints, dict) else stints
            if race:
                gap = _value(t.get("GapToLeader"))
                interval = _value(t.get("IntervalToPositionAhead"))
            else:  # practice and qualifying: gap to the fastest lap
                gap = _value(t.get("TimeDiffToFastest"))
                interval = _value(t.get("TimeDiffToPositionAhead"))
            status = ("OUT" if t.get("Retired") or t.get("Stopped") else "PIT" if t.get("InPit")
                      else "KO" if t.get("KnockedOut") else "")
            rows.append({
                "pos": int(t.get("Position") or 99), "number": num,
                "code": d.get("Tla", num), "name": (d.get("FullName") or "").title(),
                "team": d.get("TeamName", ""), "colour": "#" + d["TeamColour"] if d.get("TeamColour") else "",
                "gap": gap, "interval": interval,
                "last": _value(t.get("LastLapTime")), "best": _value(t.get("BestLapTime")),
                "laps": t.get("NumberOfLaps") or "", "pits": t.get("NumberOfPitStops") or 0,
                "tyre": (stints[-1].get("Compound") or "")[:1] if stints else "",
                "status": status,
            })
        rows.sort(key=lambda r: r["pos"])
        laps = self.raw.get("LapCount", {})
        msgs = self.raw.get("RaceControlMessages", {}).get("Messages", [])
        msgs = list(msgs.values()) if isinstance(msgs, dict) else msgs
        track = self.raw.get("TrackStatus", {})
        return {
            "series": "f1",
            "event": info.get("Meeting", {}).get("Name", ""),
            "session": info.get("Name", ""),
            "kind": "race" if race else "timed",
            "status": self.raw.get("SessionStatus", {}).get("Status", ""),
            "track": TRACK.get(str(track.get("Status", "")), track.get("Message", "")),
            "lap": laps.get("CurrentLap"), "total_laps": laps.get("TotalLaps"),
            "clock": self.raw.get("ExtrapolatedClock", {}).get("Remaining", ""),
            "rows": rows,
            "messages": [{"lap": m.get("Lap"), "text": m.get("Message", "")} for m in msgs[-6:]][::-1],
            "updated": self.changed,
        }


def _negotiate() -> tuple[str, list[str]]:
    req = urllib.request.Request(f"{BASE}/signalrcore/negotiate?negotiateVersion=1", method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=15) as r:
        token = json.load(r)["connectionToken"]
        cookies = [c.split(";")[0] for c in r.headers.get_all("Set-Cookie") or []]
    return token, cookies


async def run(state: F1State, stop: asyncio.Event) -> None:
    """Stay connected (reconnecting with backoff) until `stop` is set."""
    delay = 2
    while not stop.is_set():
        try:
            token, cookies = await asyncio.to_thread(_negotiate)
            url = f"wss://livetiming.formula1.com/signalrcore?id={urllib.parse.quote(token)}"
            headers = {"Cookie": "; ".join(cookies)} if cookies else None
            async with websockets.connect(url, additional_headers=headers, max_size=None, open_timeout=20) as ws:
                await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
                await ws.send(json.dumps({"type": 1, "invocationId": "1", "target": "Subscribe",
                                          "arguments": [TOPICS]}) + RS)
                log.info("connected to F1 live timing")
                delay = 2
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 30)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"type": 6}) + RS)  # keep-alive ping
                        continue
                    for frame in raw.split(RS):
                        if not frame:
                            continue
                        msg = json.loads(frame)
                        if msg.get("type") == 3 and isinstance(msg.get("result"), dict):
                            for topic, data in msg["result"].items():  # initial full state
                                state.apply(topic, data)
                        elif msg.get("type") == 1 and msg.get("target") == "feed":
                            topic, data = msg["arguments"][:2]
                            state.apply(topic, data)
                        elif msg.get("type") == 7:  # server closing
                            raise ConnectionError(msg.get("error") or "server closed")
        except Exception as exc:
            if stop.is_set():
                break
            log.warning("F1 live timing connection lost (%r); retrying in %ss", exc, delay)
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 60)


def _stream_lines(path: Path):
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if len(line) > 12 and line[2] == ":":
            h, m, s = line[:12].split(":")
            yield int(h) * 3600 + int(m) * 60 + float(s), json.loads(line[12:])


async def replay(state: F1State, stop: asyncio.Event, folder: Path, speed: float = 10, skip: float = 0) -> None:
    """Play an archived session (the <topic>.jsonStream files) into `state`, `speed` times real time."""
    events = sorted(((t, topic, data) for topic in TOPICS if (folder / f"{topic}.jsonStream").exists()
                     for t, data in _stream_lines(folder / f"{topic}.jsonStream")), key=lambda e: e[0])
    start = time.monotonic()
    for t, topic, data in events:
        if stop.is_set():
            return
        if t > skip:
            wait = (t - skip) / speed - (time.monotonic() - start)
            if wait > 0:
                try:
                    await asyncio.wait_for(stop.wait(), wait)
                    return
                except asyncio.TimeoutError:
                    pass
        state.apply(topic, data)
