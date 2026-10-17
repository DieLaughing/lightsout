"""Slow API requests: every one over SLOW is kept as (when, route pattern, ms, during live or quiet), never who, for
KEEP_DAYS. At 9 am Eastern the owner gets one ntfy summary of the past 24 hours, only on days that had any.

The write happens in a worker thread after the answer has gone out, so logging a slow request never makes it slower."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS slow_request (
    at REAL NOT NULL, route TEXT NOT NULL, ms INTEGER NOT NULL, live INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS slow_request_at ON slow_request (at);
"""

SLOW = 0.5           # seconds: the speed budget's "slow request" alarm
KEEP_DAYS = 14
SEND_AT = 9          # hour, Eastern
TOP = 5              # routes listed in the summary
PER_DAY = 5000       # rows kept per day at most: a stuck loop can't fill the disk
CHECK_EVERY = 600    # seconds between "is the summary due?" checks
EASTERN = ZoneInfo("America/New_York")
log = logging.getLogger("lightsout.slow")

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()
_today: tuple[str, int] = ("", 0)   # (Eastern day, rows added that day)


def init(conn: sqlite3.Connection) -> None:
    """`conn` is slow's own connection, like usage's: its commits never land inside another module's transaction."""
    global _conn, _today
    _conn, _today = conn, ("", 0)
    with _lock, conn:
        conn.executescript(SCHEMA)


def add(route: str, ms: int, live: bool, at: float | None = None) -> None:
    global _today
    at = at or time.time()
    day = datetime.fromtimestamp(at, EASTERN).date().isoformat()
    with _lock:
        n = _today[1] if _today[0] == day else 0
        if n >= PER_DAY:
            return
        _today = (day, n + 1)
        with _conn:
            if n == 0:
                _conn.execute("DELETE FROM slow_request WHERE at < ?", (at - KEEP_DAYS * 86400,))
            _conn.execute("INSERT INTO slow_request VALUES (?,?,?,?)", (at, route, ms, int(live)))


def report(end: datetime) -> dict | None:
    """The 24 hours (wall clock, so 23 or 25 across a DST change) before `end`; None if nothing was slow."""
    start = end - timedelta(days=1)
    with _lock:
        rows = _conn.execute("SELECT route, ms, live FROM slow_request WHERE at >= ? AND at < ?",
                             (start.timestamp(), end.timestamp())).fetchall()
    if not rows:
        return None
    routes: dict[str, dict] = {}
    for route, ms, live in rows:
        r = routes.setdefault(route, {"route": route, "count": 0, "live": 0, "worst": 0})
        r["count"] += 1
        r["live"] += live
        r["worst"] = max(r["worst"], ms)
    return {"count": len(rows), "live": sum(r["live"] for r in routes.values()),
            "worst": max(r["worst"] for r in routes.values()),
            "routes": sorted(routes.values(), key=lambda r: (-r["count"], -r["worst"], r["route"]))}


def _secs(ms: int) -> str:
    return f"{ms / 1000:.1f} s"


def _when(live: int, count: int) -> str:
    return "during live" if live == count else "quiet" if not live else f"{live} during live"


def message(rep: dict) -> tuple[str, str]:
    n, live = rep["count"], rep["live"]
    title = f"Lights Out: {n} slow request{'s' * (n != 1)} yesterday"
    head = f"Over {_secs(int(SLOW * 1000))}: {live} during live, {n - live} quiet. Worst {_secs(rep['worst'])}."
    lines = [f"{r['route']}: {r['count']}×, worst {_secs(r['worst'])} ({_when(r['live'], r['count'])})"
             for r in rep["routes"][:TOP]]
    if (more := len(rep["routes"]) - TOP) > 0:
        lines.append(f"+ {more} more route{'s' * (more != 1)}")
    return title, "\n".join([head, *lines])


async def send_due(store, notify, now: datetime | None = None) -> bool:
    """Send today's summary if it's past SEND_AT and not sent yet (a quiet day counts as done). A failed push is
    tried again on the next check. True when a summary went out."""
    now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
    key = f"slow-summary:{now.date().isoformat()}"
    if now.hour < SEND_AT or store.was_sent(key):
        return False
    rep = report(now.replace(hour=SEND_AT, minute=0, second=0, microsecond=0))
    if rep and not await notify(*message(rep), tags="snail"):
        return False
    store.mark_sent([key])
    return bool(rep)


async def loop(store, notify) -> None:
    while True:
        try:
            await send_due(store, notify)
        except Exception:
            log.exception("slow-request summary failed")
        await asyncio.sleep(CHECK_EVERY)
