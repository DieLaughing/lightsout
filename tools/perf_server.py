"""Lights Out with made-up racing data and no network, for tools/perfcheck.py and CI. Every series gets a season of
events around today (practice, qualifying, race), a grid, standings and Race Wire stories, and the owner follows a few
drivers, so pages have a realistic amount to draw. Any outside request fails at once (the app treats it as a source
being down), so timings measure this app's own code and nothing waits on (or bothers) the real sources.

    .venv/bin/python tools/perf_server.py --port 8792 --data /tmp/lightsout-perf

Signs everyone in as LIGHTSOUT_ADMIN_EMAIL (default perf@example.com) and runs without the refresh loops or live timing
(which would otherwise connect to the real feeds whenever a made-up session is on)."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS, DRIVERS = 14, 24      # per series


class Offline(Exception):
    """An outside request: perf runs never leave the machine."""


def block_network() -> None:
    import asyncio
    import urllib.request

    import websockets
    from curl_cffi.requests import AsyncSession
    from lightsout.live.hub import LiveHub

    async def refuse(self, method, url, *a, **kw):
        raise Offline(url)

    def refuse_sync(url, *a, **kw):
        raise Offline(getattr(url, "full_url", url))

    def refuse_ws(uri, *a, **kw):
        raise Offline(uri)

    async def idle(self):
        await asyncio.Event().wait()
    AsyncSession.request = refuse
    urllib.request.urlopen = refuse_sync
    websockets.connect = refuse_ws
    LiveHub.loop = LiveHub.events_loop = idle   # live timing (F1/FIA hubs, NASCAR/IndyCar/WRC polls) stays off


def seed(store, email: str) -> None:
    from lightsout.models import PRACTICE, QUALIFYING, RACE, AdapterResult, Entry, Event, Session, Standings, StandingRow
    from lightsout.series import SERIES
    today = datetime.now(timezone.utc).date()
    news = []
    for i, s in enumerate(SERIES):
        drivers = [f"{s.short} Driver {n}" for n in range(1, DRIVERS + 1)]
        teams = [f"{s.short} Team {n // 2 + 1}" for n in range(DRIVERS)]
        events = []
        for e in range(EVENTS):
            first = today + timedelta(days=14 * e - 84 + i % 7)
            at = lambda d, h: datetime.combine(first + timedelta(days=d), time(h), timezone.utc)  # noqa: E731
            events.append(Event(key=f"{first.year}-round-{e + 1}", season=first.year, name=f"{s.short} Round {e + 1}",
                                venue=f"Circuit {e + 1}", country="USA", start_day=first, end_day=first + timedelta(days=2),
                                sessions=[Session("fp1", "Practice 1", PRACTICE, start=at(0, 14)),
                                          Session("q", "Qualifying", QUALIFYING, start=at(1, 15)),
                                          Session("race", "Race", RACE, start=at(2, 18))], entrants=drivers))
        store.save(s.id, AdapterResult(events, sorted({e.season for e in events}),
                                       [Entry(name=d, team=t, number=str(n + 1)) for n, (d, t) in enumerate(zip(drivers, teams))]))
        store.save_standings(s.id, Standings(
            drivers=[StandingRow(pos=n + 1, name=d, team=t, points=300 - 11 * n) for n, (d, t) in enumerate(zip(drivers, teams))],
            teams=[StandingRow(pos=n + 1, name=f"{s.short} Team {n + 1}", points=500 - 30 * n) for n in range(DRIVERS // 2)],
            last_event=f"{s.short} Round 6", season_label=str(today.year)))
        for n in range(6):
            news.append({"id": f"perf-{s.id}-{n}", "time": (datetime.now(timezone.utc) - timedelta(hours=7 * n + i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "title": f"{s.name}: story {n + 1}", "text": "A made-up story for speed checks.",
                         "url": f"https://example.com/{s.id}/{n}", "source": "Perf", "series": [s.id],
                         "drivers": [drivers[n]], "teams": [teams[n]]})
    store.add_news(news)
    store.save_settings(email, {"follows": [{"name": f"{s.short} Driver 1"} for s in SERIES[:6]]})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--data", required=True, help="an empty folder for the database")
    args = ap.parse_args()
    email = os.environ.setdefault("LIGHTSOUT_ADMIN_EMAIL", "perf@example.com")
    os.environ.update(LIGHTSOUT_DATA=args.data, LIGHTSOUT_LOCAL_EMAIL=email, LIGHTSOUT_NO_REFRESH="1")
    sys.path.insert(0, str(ROOT))
    block_network()
    from lightsout import app as app_module
    app_module.store.set_status(email, "approved")
    seed(app_module.store, email)
    import uvicorn
    uvicorn.run(app_module.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
