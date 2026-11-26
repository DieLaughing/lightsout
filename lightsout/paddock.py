"""Winter in the paddock: what Home shows for a series between seasons.

A series is in its off-season when its last race is done, nothing races for LEAD_DAYS, and the next race opens a new
season (or comes after a gap of BREAK_DAYS or more, or isn't on the calendar yet). A summer break isn't an
off-season. For each one: the champion (when the season is over), the first race of the next season, testing
(from the schedule and data/testing.yaml) and the driver/team moves from the news feeds the Wire already reads.

Moves are kept longer than the Wire's 30 days (MOVES_DAYS), in the cache table under MOVES_KEY, so a signing from
October is still on the list in February.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

from . import colors, places
from .models import EVENT, RACE, RALLY_DAY, SPRINT

RACING = (RACE, SPRINT, RALLY_DAY, EVENT)
LEAD_DAYS = 10      # inside this many days of the first race, Home goes back to its race-weekend card
BREAK_DAYS = 75     # a gap this long is an off-season even inside one numbered season (Formula E's summer)
MOVES_DAYS = 150
MOVES_KEY = "paddock:moves"
SHOW_MOVES = 6
TESTING = Path(__file__).resolve().parent / "data" / "testing.yaml"

# A headline about a driver or team move. Race-day words rule a headline out ("Verstappen extends lead").
MOVE = re.compile(r"\b(sign(s|ed|ing)?|re-?signs?|joins?|joined|joining|replac(e|es|ed|ing|ement)|seat|contract|"
                  r"extension|leav(e|es|ing)|depart(s|ure|ing)?|exits?|splits? with|parts? ways|moves? to|switch(es)? to|"
                  r"retire(s|d|ment)?|promot(ed|es|ion)|line-?up|axed|dropped|ousted|graduat(e|es|ion)|"
                  r"confirm(s|ed)? (as|for)|stays? (with|at)|remains? (with|at)|silly season)\b", re.I)
NOT_MOVE = re.compile(r"\b(pole|qualifying|practice|fp[123]|wins?|won|victory|podium|crash(es|ed)?|penalt(y|ies)|"
                      r"lead|leads|result|results|report)\b", re.I)


def is_move(item: dict) -> bool:
    title = item.get("title") or ""
    return bool((item.get("drivers") or item.get("teams")) and MOVE.search(title) and not NOT_MOVE.search(title))


TEST_EVENT = re.compile(r"\b(tests?|testing|roar)\b", re.I)   # IMSA's pre-season test is the Roar Before the Rolex 24


def remember(store, items: list[dict], now: datetime | None = None) -> int:
    """Adds the move headlines among `items` to the kept list (drops ones older than MOVES_DAYS). Returns how many were new."""
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=MOVES_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hit = store.cached(MOVES_KEY)
    kept = {m["id"]: m for m in (hit[0] if hit else []) if m["time"] >= cut}
    before = len(kept)
    for n in items:
        if n["id"] not in kept and n["time"] >= cut and is_move(n):
            kept[n["id"]] = {k: n.get(k) for k in ("id", "time", "title", "url", "source", "series", "drivers", "teams")}
    if not hit or {m["id"] for m in hit[0]} != kept.keys():   # something added or aged out
        store.save_cached(MOVES_KEY, sorted(kept.values(), key=lambda m: m["time"], reverse=True))
    return len(kept) - before


@lru_cache(maxsize=1)
def _testing() -> dict:
    data = yaml.safe_load(TESTING.read_text()) or {}
    return {sid: [{"name": t["name"], "place": t.get("place") or "", "start": str(t["start"]), "end": str(t.get("end") or t["start"])}
                  for t in rows or []] for sid, rows in data.items()}


def testing(sid: str) -> list[dict]:
    return _testing().get(sid, [])


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def winter(store, series_ids: list[str], now: datetime | None = None) -> list[dict]:
    """One entry per off-season series in `series_ids`, in the order given."""
    now = now or datetime.now(timezone.utc)
    stamp, today = now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%d")
    hit = store.cached(MOVES_KEY)
    moves = hit[0] if hit else []
    out = []
    for sid in series_ids:
        last, nxt = store.race_edges(sid, stamp, RACING)
        if not last and not nxt:
            continue  # nothing on file at all
        if nxt and _days(today, nxt["sort_utc"]) <= LEAD_DAYS:
            continue
        # No past race on file: the finished season has dropped off the source (IndyCar, WRC...), only the next is in.
        new_season = not last or not nxt or nxt["season"] != last["season"]
        if not new_season and _days(last["sort_utc"], nxt["sort_utc"]) < BREAK_DAYS:
            continue
        out.append({"series": sid, "season": last["season"] if last else nxt["season"] - 1,
                    "next": _next(nxt), "champion": _champion(store, sid, last, nxt) if new_season else None,
                    "tests": _tests(store, sid, last, nxt, today), "moves": _moves(moves, sid, last)})
    return out


def _next(s: dict | None) -> dict | None:
    if not s:
        return None
    place = places.for_event(s["venue"], s["event_name"])
    return {"event_key": s["event_key"], "event": s["event_name"], "session": s["name"], "start_utc": s["start_utc"],
            "day": s["day"], "season": s["season"], "place": place["label"] if place else s["venue"] or s["country"] or ""}


def _champion(store, sid: str, last: dict | None, nxt: dict | None) -> dict | None:
    """The finished season's leaders. The table must be saved after the last race (not the pre-finale one). One whose
    label has a year must be the last race's season (or, with no past race on file, one before the next race's); one
    without (NASCAR's "Points") counts until the source resets it to zero for the new season. `season` is the label's
    own ("2025-26", "Season 12"), else the season number."""
    table = store.standings(sid) or {}
    drivers, teams = table.get("drivers") or [], table.get("teams") or []
    if not any(r["points"] for r in (drivers or teams)[:1]):
        return None
    if last and (table.get("updated") or "") < (last["start_utc"] or last["sort_utc"]):
        return None
    year = re.search(r"\b(\d{4})(?:[-–/]\d{2,4})?\b", table.get("season_label") or "")
    finished = last["season"] if last else nxt["season"] - 1
    if year and (int(year.group(1)) != last["season"] if last else int(year.group(1)) >= nxt["season"]):
        return None
    d, t = (drivers or [None])[0], (teams or [None])[0]
    named = re.match(r"Season \d+", table.get("season_label") or "")
    return {"season": year.group(0) if year else named.group(0) if named else str(finished),
            "driver": d and {"name": d["name"], "team": d.get("team") or "", "color": colors.for_team(d.get("team") or "", sid)},
            "team": t and {"name": t["name"], "color": colors.for_team(t["name"], sid)}}


def _tests(store, sid: str, last: dict | None, nxt: dict | None, today: str) -> list[dict]:
    # From the last race (or from anything on file when it's gone); tests already over drop off below.
    start, end = last["day"] if last else "", nxt["event_start"] if nxt else None
    found = [{"name": e["name"], "place": (places.for_event(e["venue"], e["name"]) or {}).get("label") or e["venue"] or "",
              "start": e["start_day"], "end": e["end_day"] or e["start_day"]}
             for e in store.events_between(sid, start, end) if TEST_EVENT.search(e["name"])]
    found += [t for t in testing(sid) if t["start"] > start and (not end or t["start"] < end)]
    return sorted((t for t in found if t["end"] >= today), key=lambda t: t["start"])


def _moves(moves: list[dict], sid: str, last: dict | None) -> list[dict]:
    """The series' newest moves, from a fortnight before its last race (silly season starts before the finale);
    everything kept when the last race isn't on file."""
    since = (date.fromisoformat(last["day"][:10]) - timedelta(days=14)).isoformat() if last else ""
    return [m for m in moves if sid in m["series"] and m["time"][:10] >= since][:SHOW_MOVES]
