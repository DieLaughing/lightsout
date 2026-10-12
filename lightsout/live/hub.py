"""Starts live feeds shortly before a session and stops them after it ends.

Only connected while something is on track, so the Pi does nothing between sessions.
Testing: LIGHTSOUT_REPLAY_F1=<folder of .jsonStream files> replays an archived F1 session,
LIGHTSOUT_WRC_EVENT=<eventId> follows a past rally, LIGHTSOUT_FORCE_LIVE=f2,f3 connects regardless of schedule.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..db import Store
from ..names import Tagger
from . import f1, fia, indycar, nascar, wrc

log = logging.getLogger("lightsout.live")

BEFORE = timedelta(minutes=15)   # connect this long before a session
AFTER = timedelta(minutes=45)    # and stay this long after its scheduled end (delays, red flags)
KEEP = 3 * 3600                  # show the final state this many seconds after disconnecting

# series -> (state factory, live runner, archive replayer or None)
FEEDS = {
    "f1": (f1.F1State, f1.run, f1.replay),
    "f2": (fia.make_state("f2"), fia.run, None),
    "f3": (fia.make_state("f3"), fia.run, None),
    "f1a": (fia.make_state("f1a"), fia.run, None),
    "wrc": (wrc.WrcState, wrc.run, None),
    "indycar": (indycar.IndyState, indycar.run, None),
    "cup": (nascar.make_state("cup"), nascar.run, None),
    "noaps": (nascar.make_state("noaps"), nascar.run, None),
    "truck": (nascar.make_state("truck"), nascar.run, None),
}
# Series whose sessions are whole days (rallies): live while today or yesterday (UTC) is a rally day.
DAY_FEEDS = {"wrc"}


class Feed:
    def __init__(self, series: str):
        make_state, self._run, self._replay = FEEDS[series]
        self.series = series
        self.state = make_state()
        self.stop = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.stopped_at: float | None = None

    def start(self, replay_dir: str | None = None):
        if replay_dir and self._replay:
            speed = float(os.environ.get("LIGHTSOUT_REPLAY_SPEED", "1"))
            skip = float(os.environ.get("LIGHTSOUT_REPLAY_SKIP", "0"))
            self.task = asyncio.create_task(self._replay(self.state, self.stop, Path(replay_dir), speed, skip))
        else:
            self.task = asyncio.create_task(self._run(self.state, self.stop))
        log.info("%s live feed started%s", self.series, " (replay)" if replay_dir else "")

    def end(self):
        self.stop.set()
        self.stopped_at = time.time()
        log.info("%s live feed stopped", self.series)

    @property
    def running(self) -> bool:
        return self.task is not None and not self.stop.is_set()


EVENT_POLL = 5        # seconds between collecting race events from the running feeds
TAGGER_TTL = 600      # rebuild the driver/team tagger from the grids this often


class LiveHub:
    def __init__(self, store: Store):
        self.store = store
        self.feeds: dict[str, Feed] = {}
        self.alerts = None          # set by the app; told about every new race event
        self._tagger: Tagger | None = None
        self._tagger_at = 0.0

    def tagger(self) -> Tagger:
        if not self._tagger or time.time() - self._tagger_at > TAGGER_TTL:
            self._tagger, self._tagger_at = Tagger(self.store.entries()), time.time()
        return self._tagger

    def _schedule_slot(self, series: str, now: datetime) -> tuple[str | None, str | None]:
        """The schedule's event and session that a live feed belongs to right now."""
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        rows = self.store.sessions((now - timedelta(hours=8)).strftime(fmt), (now + BEFORE).strftime(fmt), [series])
        if not rows:
            return None, None
        timed = [r for r in rows if r["start_utc"] and r["start_utc"] <= (now + BEFORE).strftime(fmt)]
        r = (timed or rows)[-1]
        return r["event_key"], r["name"]

    async def events_loop(self):
        """Collect new race events from every feed, tag them and store them; alert on the new ones."""
        while True:
            try:
                now = datetime.now(timezone.utc)
                batch = []
                for series, feed in self.feeds.items():
                    take = getattr(getattr(feed.state, "log", None), "take", None)
                    if not take:
                        continue
                    events = take()
                    if not events:
                        continue
                    event_key, _ = self._schedule_slot(series, now)
                    tagger = self.tagger()
                    finish = None
                    for e in events:
                        drivers, teams = tagger.tag(" ; ".join(e["names"]) + " ; " + e["text"], [series])
                        e.update(event_key=event_key, drivers=drivers, teams=teams)
                        if e["kind"] == "result":
                            finish = finish or {r["name"]: r["pos"] for r in feed.state.snapshot().get("rows", [])
                                                if r.get("name") and r.get("pos", 99) < 99}
                            e["positions"] = finish
                    batch += events
                fresh = self.store.add_events(batch) if batch else []
                if fresh and self.alerts:
                    await self.alerts.on_events(fresh)
            except Exception:
                log.exception("race event collection failed")
            await asyncio.sleep(EVENT_POLL)

    def _wanted(self, now: datetime) -> set[str]:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        rows = self.store.sessions((now - AFTER).strftime(fmt), (now + BEFORE).strftime(fmt), list(FEEDS))
        wanted = {r["series"] for r in rows if r["start_utc"]}
        days = {(now - timedelta(days=1)).date().isoformat(), now.date().isoformat()}
        day_rows = self.store.sessions((now - timedelta(days=2)).strftime(fmt), (now + timedelta(days=1)).strftime(fmt),
                                       list(DAY_FEEDS))
        return wanted | {r["series"] for r in day_rows if not r["start_utc"] and r["day"] in days}

    async def loop(self):
        replay = {s: os.environ.get(f"LIGHTSOUT_REPLAY_{s.upper()}") for s in FEEDS}
        if os.environ.get("LIGHTSOUT_WRC_EVENT"):
            replay["wrc"] = "event"  # run the normal poller pinned to that past rally
        while True:
            try:
                forced = {x for x in os.environ.get("LIGHTSOUT_FORCE_LIVE", "").split(",") if x in FEEDS}
                wanted = self._wanted(datetime.now(timezone.utc)) | {s for s, d in replay.items() if d} | forced
                for series in FEEDS:
                    feed = self.feeds.get(series)
                    if series in wanted and not (feed and feed.running):
                        self.feeds[series] = Feed(series)
                        self.feeds[series].start(replay.get(series))
                    elif series not in wanted and feed and feed.running:
                        feed.end()
            except Exception:
                log.exception("live hub error")
            await asyncio.sleep(30)

    def snapshot(self, series: str) -> dict | None:
        feed = self.feeds.get(series)
        if not feed or not feed.state.raw:
            return None
        if not feed.running and time.time() - (feed.stopped_at or 0) > KEEP:
            return None
        return {**feed.state.snapshot(), "connected": feed.running}

    def summary(self) -> list[dict]:
        out = []
        for series in self.feeds:
            snap = self.snapshot(series)
            if snap:
                out.append({k: snap[k] for k in ("series", "event", "session", "status", "track", "lap",
                                                  "total_laps", "connected")})
        return out
