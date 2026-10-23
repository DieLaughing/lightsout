"""F1 Academy extras: who backs each driver, who's a wild card, and who to watch at a round.

Every F1 ACADEMY car runs in a backer's colours: an F1 team (Ferrari, McLaren...) or a sponsor (LEGO, PUMA...).
That's the hook for F1 fans, so the app shows it next to the name wherever F1A drivers are listed, and marks the ones
backed by an F1 team so following McLaren in F1 also points at McLaren's F1A driver. The roster comes from the
official drivers page (one request), saved in the profile table and refreshed by profiles.loop; pages only ever read
the saved copy, so they never wait on f1academy.com.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from . import colors
from .adapters import f1academy
from .db import Store
from .follow import matches, normalise
from .names import team_config

log = logging.getLogger("lightsout.f1a")

KEY = "f1a:roster"
TTL = timedelta(hours=12)


async def refresh(store: Store) -> list[dict]:
    """The roster, fetched again when the saved copy is older than TTL (or missing); the saved copy if that fails."""
    saved = store.cached(KEY)
    if saved and datetime.now(timezone.utc) - datetime.fromisoformat(saved[1].replace("Z", "+00:00")) < TTL:
        return saved[0]
    try:
        rows = await f1academy.roster()
    except Exception as exc:
        log.warning("F1 Academy roster failed: %r", exc)
        return saved[0] if saved else []
    store.save_cached(KEY, rows)
    return rows


def roster(store: Store) -> list[dict]:
    return (store.cached(KEY) or ([],))[0]


def find(rows: list[dict], name: str) -> dict | None:
    key = normalise(name)
    return next((r for r in rows if normalise(r["name"]) == key), None) or next((r for r in rows if matches(name, r["name"])), None)


@lru_cache(maxsize=1)
def _f1_names() -> dict[str, str]:
    out = {}
    for team, info in (team_config().get("f1") or {}).items():
        for n in [team, *((info or {}).get("aliases") or []), *((info or {}).get("spell") or [])]:
            out[normalise(n)] = team
    return out


def f1_team(backer: str) -> str | None:
    """'Red Bull Racing' -> 'Red Bull Racing'; sponsors (LEGO, 'McLaren Oxagon') -> None. Exact names only."""
    return _f1_names().get(normalise(backer))


def tag(store: Store, rows: list[dict], key: str = "name", colour: bool = False) -> list[dict]:
    """Adds backer / f1_team (+ its colour) / wildcard to each row whose driver is on the roster; others are left alone.
    colour: also paint the row's bar in their F1A team's colour (live timing knows no teams)."""
    people = roster(store)
    for r in rows:
        d = find(people, r.get(key) or "") if r.get(key) else None
        if d:
            r["backer"], r["f1_team"] = d["backer"], f1_team(d["backer"])
            if r["f1_team"]:
                r["f1_color"] = colors.for_team(r["f1_team"], "f1")
            r["wildcard"] = r.get("wildcard") or d["wildcard"]
            if colour and not r.get("colour"):
                r["colour"] = colors.for_team(d["team"], "f1a")
    return rows


def watch(store: Store, ev: dict, place: dict | None) -> dict | None:
    """Before a round: the championship top 3 (and how far apart), wild cards, and drivers racing at home."""
    people = roster(store)
    if not people:
        return None
    table = (store.standings("f1a") or {}).get("drivers") or []
    leaders = [{"pos": r["pos"], "name": r["name"], "points": r["points"]} for r in table[:3]]
    country, cc = normalise(ev.get("country") or ""), ((place or {}).get("cc") or "").upper()
    home = [d for d in people if (country and normalise(d["country"]) == country) or (cc and d["cc"].upper() == cc)]
    wild = [d for d in people if d["wildcard"]]
    out = {"leaders": tag(store, leaders), "home": tag(store, [{"name": d["name"]} for d in home]),
           "wildcards": tag(store, [{"name": d["name"]} for d in wild])}
    return out if any(out.values()) else None
