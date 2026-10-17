"""Slow-request log: route patterns recorded off the event loop, the 9am summary only on days with slow requests."""
import asyncio
import sqlite3
import time
from datetime import datetime

from lightsout import slow
from lightsout.slow import EASTERN
from test_access import client  # noqa: F401  (the app fixture)

NINE = datetime(2026, 10, 17, 9, 0, tzinfo=EASTERN)


def _setup():
    slow.init(sqlite3.connect(":memory:", check_same_thread=False))


class FakeStore:
    def __init__(self):
        self.keys = set()

    def was_sent(self, k):
        return k in self.keys

    def mark_sent(self, ks):
        self.keys.update(ks)


def test_report_groups_routes_inside_the_window():
    _setup()
    t = NINE.timestamp()
    slow.add("/api/live/{series_id}", 2400, True, at=t - 3600)
    slow.add("/api/live/{series_id}", 700, False, at=t - 7200)
    slow.add("/api/home", 900, False, at=t - 60)
    slow.add("/api/wire", 600, False, at=t - 86400 - 1)   # before the window
    slow.add("/api/wire", 600, False, at=t)               # after it: today's
    rep = slow.report(NINE)
    assert rep["count"] == 3 and rep["live"] == 1 and rep["worst"] == 2400
    assert [(r["route"], r["count"], r["worst"]) for r in rep["routes"]] == [("/api/live/{series_id}", 2, 2400), ("/api/home", 1, 900)]
    title, body = slow.message(rep)
    assert "3 slow requests" in title
    assert body.splitlines() == ["Over 0.5 s: 1 during live, 2 quiet. Worst 2.4 s.",
                                 "/api/live/{series_id}: 2×, worst 2.4 s (1 during live)",
                                 "/api/home: 1×, worst 0.9 s (quiet)"]


def test_summary_once_a_day_after_nine_and_only_with_slow_requests():
    _setup()
    store, sent = FakeStore(), []

    async def notify(title, body, **kw):
        sent.append(title)
        return True
    run = lambda now: asyncio.run(slow.send_due(store, notify, now))
    assert not run(NINE.replace(hour=8, minute=59))
    assert not run(NINE) and not sent                       # quiet day: nothing sent, but done for today
    assert store.was_sent("slow-summary:2026-10-17")
    slow.add("/api/wire", 800, False, at=NINE.timestamp() + 60)
    tomorrow = NINE.replace(day=18, minute=5)
    assert run(tomorrow) and len(sent) == 1
    assert not run(tomorrow.replace(hour=15)) and len(sent) == 1


def test_failed_push_is_retried():
    _setup()
    store, ok = FakeStore(), [False]
    slow.add("/api/wire", 800, False, at=NINE.timestamp() - 60)

    async def notify(title, body, **kw):
        return ok[0]
    assert not asyncio.run(slow.send_due(store, notify, NINE))
    ok[0] = True
    assert asyncio.run(slow.send_due(store, notify, NINE.replace(minute=10)))


def test_per_day_cap_and_cleanup():
    _setup()
    old = NINE.timestamp() - (slow.KEEP_DAYS + 1) * 86400
    slow.add("/api/wire", 600, False, at=old)
    for _ in range(slow.PER_DAY + 5):
        slow.add("/api/wire", 600, False, at=NINE.timestamp())
    rows = slow._conn.execute("SELECT COUNT(*), MIN(at) FROM slow_request").fetchone()
    assert rows == (slow.PER_DAY, NINE.timestamp())     # the old day was dropped by the first add of a new one


def test_middleware_records_the_route_pattern(client, monkeypatch):
    c, who, _, _ = client
    who["email"] = "owner@example.com"
    monkeypatch.setattr(slow, "SLOW", -1)                  # everything counts as slow
    assert c.get("/api/me").status_code == 200
    assert c.get("/api/live/nope").status_code == 200
    assert c.get("/api/live/nope/missing").status_code == 404     # no route at all
    for _ in range(50):
        routes = {r[0] for r in slow._conn.execute("SELECT route FROM slow_request")}
        if {"/api/me", "/api/live/{series_id}", "(no route)"} <= routes:
            break
        time.sleep(0.02)
    assert {"/api/me", "/api/live/{series_id}", "(no route)"} <= routes
