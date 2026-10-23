"""Betting odds next to races, drivers and Pick'em duels (DraftKings, via tools/odds_fetch.py).

DraftKings refuses data-centre addresses, so the app never asks it: a machine on a home connection runs
tools/odds_fetch.py on a timer and copies odds.json here (LIGHTSOUT_ODDS_FILE, default <data>/odds.json). This module
only reads that file (re-read when it changes). Odds older than STALE aren't shown: a stale price is worse than none.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .follow import matches, normalise

log = logging.getLogger("lightsout.odds")

STALE = timedelta(hours=6)
_path: Path | None = None
_cache: tuple[float, dict] = (0.0, {})
_warned = 0.0


def setup(data_dir: Path) -> None:
    global _path
    _path = Path(os.environ.get("LIGHTSOUT_ODDS_FILE") or data_dir / "odds.json")


def _load() -> dict:
    """The file's contents when fresh enough, else {} (and a warning at most once an hour)."""
    global _cache, _warned
    if not _path:
        return {}
    try:
        mtime = _path.stat().st_mtime
    except FileNotFoundError:
        return {}
    if mtime != _cache[0]:
        try:
            _cache = (mtime, json.loads(_path.read_text()))
        except (OSError, ValueError) as exc:
            log.warning("odds file unreadable: %r", exc)
            return {}
    data = _cache[1]
    fetched = data.get("fetched") or ""
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched.replace("Z", "+00:00"))
    except ValueError:
        return {}
    if age > STALE:
        if time.time() - _warned > 3600:
            _warned = time.time()
            log.warning("odds file is stale (fetched %s): is the odds fetcher running?", fetched)
        return {}
    return data


def _market_name(name: str) -> str:
    return "Race Winner" if name == "Winner" else name


def for_event(series: str, start_day: str | None, end_day: str | None) -> dict | None:
    """The odds for one race weekend: the bookmaker's event dated within the weekend (a day's slack either side)."""
    data = _load()
    if not data or not start_day:
        return None
    lo = date.fromisoformat(start_day) - timedelta(days=1)
    hi = date.fromisoformat(end_day or start_day) + timedelta(days=1)
    for e in data.get("events") or []:
        if e.get("series") == series and lo <= date.fromisoformat(e["start"][:10]) <= hi:
            return {"source": data.get("source", ""), "fetched": data["fetched"], "name": e["name"],
                    "markets": [{"name": _market_name(m["name"]), "rows": m["rows"]} for m in e["markets"]]}
    return None


def _ends(name: str) -> tuple[str, str]:
    w = normalise(name).split()
    return (w[0], w[-1]) if w else ("", "")


def find(rows: list[dict], name: str) -> dict | None:
    """Exact, then one name inside the other, then first + last name alike: grids and bookmakers disagree on middle
    names ("Austin J Hill" / "Austin Hill", "John H. Nemechek" / "John Hunter Nemechek")."""
    key = normalise(name)
    return (next((r for r in rows if normalise(r["name"]) == key), None)
            or next((r for r in rows if matches(name, r["name"]) or matches(r["name"], name)), None)
            or next((r for r in rows if _ends(r["name"]) == _ends(name) and " " in key), None))


def for_driver(ev: dict | None, name: str) -> list[dict]:
    """One driver's line in each market of an event's odds: [{market, american, pct}]."""
    out = []
    for m in (ev or {}).get("markets") or []:
        if r := find(m["rows"], name):
            out.append({"market": m["name"], "american": r["american"], "pct": r["pct"]})
    return out


def winner(ev: dict | None, name: str) -> dict | None:
    return next((x for x in for_driver(ev, name) if x["market"] == "Race Winner"), None)


# A Pick'em duel asks who finishes ahead, so it shows the market that tells the two apart: the race winner while one of
# them has a real chance, else top 10 / top 5 / podium. Two drivers on the same price (back markers all sit on the
# bookmaker's floor, +50000) say nothing, so that market is skipped. If no market separates them, the duel is even:
# the first market where they have a real chance is shown, marked even (teammates often share every price).
DUEL_MARKETS = ("Race Winner", "Podium Finish", "Top 3 Finish", "Top 5 Finish", "Top 10 Finish")
WIN_FLOOR = 2.0  # %: below this for both, win prices are noise


def duel(ev: dict | None, a: str, b: str) -> tuple[dict, dict] | None:
    la, lb = ({x["market"]: x for x in for_driver(ev, n)} for n in (a, b))
    for m in DUEL_MARKETS:
        x, y = la.get(m), lb.get(m)
        if x and y and x["american"] != y["american"] and (m != "Race Winner" or max(x["pct"], y["pct"]) >= WIN_FLOOR):
            return x, y
    for m in DUEL_MARKETS:
        x, y = la.get(m), lb.get(m)
        if x and y and (m != "Race Winner" or x["pct"] >= WIN_FLOOR):
            return {**x, "even": True}, {**y, "even": True}
    return None
