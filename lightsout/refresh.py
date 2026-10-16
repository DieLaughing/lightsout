"""Keeps schedules fresh: daily normally, hourly for a series racing within a week."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from . import adapters, picks, teamnames
from .db import Store
from .series import SERIES

log = logging.getLogger("lightsout.refresh")

DAILY = timedelta(hours=24)
RACE_WEEK = timedelta(hours=1)
RETRY = timedelta(minutes=30)


def seasons_now() -> list[int]:
    year = datetime.now(timezone.utc).year
    return [year, year + 1]


async def refresh_series(store: Store, series_id: str) -> None:
    s = next(x for x in SERIES if x.id == series_id)
    try:
        result = await adapters.load(s.adapter).fetch(series_id, seasons_now())
        if not result.events:
            raise RuntimeError("source returned no events")
        teamnames.entries(series_id, result.entries)
        counts = store.save(series_id, result)
        store.record_run(series_id, True, counts=counts)
        log.info("%s: %d events, %d sessions", series_id, *counts)
    except Exception as exc:  # one broken source must never take the others down
        log.warning("%s refresh failed: %r", series_id, exc)
        store.record_run(series_id, False, error=f"{type(exc).__name__}: {exc}"[:500])
        return
    module = adapters.load(s.adapter)
    if adapters.has_standings(series_id, s.adapter):
        try:
            st = await module.standings(series_id)
            if st and (st.drivers or st.last_results or st.qualifying):
                grid = {e["name"]: e["team"] for e in store.entries(series_id) if e.get("team")}
                store.save_standings(series_id, teamnames.standings(series_id, st, grid))
                picks.grade(store, series_id)  # a new Last race scores that race's Pick'em
                season = season_of(st.season_label)
                store.save_round(series_id, season, st.last_event, datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                 {r.name: [r.pos, r.points] for r in st.last_results if r.name})
                await backfill(store, module, series_id, season)
        except Exception as exc:  # standings are a bonus; keep the last good copy
            log.warning("%s standings failed: %r", series_id, exc)


def season_of(label: str) -> int:
    """The standings' season: a leading year in its label ("2026 · after round 19"), else this year."""
    m = re.match(r"(\d{4})\b", label or "")
    return int(m.group(1)) if m else datetime.now(timezone.utc).year


_backfilled: dict[str, str] = {}  # series -> day of the last backfill (once a day is plenty)


async def backfill(store: Store, module, series_id: str, season: int) -> None:
    """Earlier races of the season, from adapters that can list them (history()), for the season form bars.
    Other series build their form from here on, one Last race at a time."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not hasattr(module, "history") or _backfilled.get(series_id) == today:
        return
    _backfilled[series_id] = today
    try:
        for r in await module.history(series_id, season, store.round_events(series_id, season)):
            store.save_round(series_id, season, r["event"], r["day"], r["rows"])
    except Exception as exc:
        log.warning("%s history failed: %r", series_id, exc)


def _due(store: Store, series_id: str, now: datetime, startup: bool = False) -> bool:
    run = store.runs().get(series_id)
    if not run or not run["last_try"]:
        return True
    last_try = datetime.fromisoformat(run["last_try"].replace("Z", "+00:00"))
    if not run["last_ok"] or run["error"]:
        return startup or now - last_try >= RETRY
    # Race week = a session in the next 7 days or the last 2 (so results and standings land quickly).
    week = store.sessions((now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"), [series_id])
    return now - last_try >= (RACE_WEEK if week else DAILY)


async def refresh_due(store: Store, startup: bool = False) -> None:
    now = datetime.now(timezone.utc)
    due = [s.id for s in SERIES if _due(store, s.id, now, startup)]
    # A few at a time: polite to the sources, and the Pi has 4 cores.
    sem = asyncio.Semaphore(4)

    async def one(sid: str):
        async with sem:
            await refresh_series(store, sid)

    await asyncio.gather(*(one(sid) for sid in due))


CHECKPOINT_EVERY = 120  # seconds between refresh checks (and WAL checkpoints); sources keep their own schedules


async def loop(store: Store) -> None:
    startup = True  # after a deploy, retry failed sources straight away
    store.manual_checkpoints()  # checkpoints happen here between passes, never inside a request's save
    while True:
        try:
            await refresh_due(store, startup)
            startup = False
        except Exception:
            log.exception("refresh loop error")
        try:
            await asyncio.to_thread(store.checkpoint)
        except Exception as exc:
            log.warning("checkpoint failed: %r", exc)
        await asyncio.sleep(CHECKPOINT_EVERY)
