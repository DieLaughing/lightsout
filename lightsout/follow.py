"""Where is a followed driver (or team) racing? Searches every grid and published entry list.

Drivers move between series (an F1 driver doing Le Mans, a rally driver doing F1 Academy), so a
follow is just a name: we look for it everywhere and report each place it appears, the next
session we can confirm, and anything that appeared recently ("newly entered").
Name matching mirrors web/match.js: accents, case and small misspellings are forgiven.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

from .db import Store
from .series import BY_ID

NEW_FOR = timedelta(days=14)
# Series where crews choose their events: a roster ("registered") never confirms a session,
# only the event's published entry list does.
PICK_EVENTS = {"wrc", "ara", "erx", "w2rc"}


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def similarity(a: str, b: str) -> float:
    """Jaro-Winkler, 0..1."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    rng = max(0, max(len(a), len(b)) // 2 - 1)
    am, bm = [False] * len(a), [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - rng), min(len(b), i + rng + 1)):
            if not bm[j] and b[j] == ch:
                am[i] = bm[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    t, k = 0, 0
    for i in range(len(a)):
        if am[i]:
            while not bm[k]:
                k += 1
            t += a[i] != b[k]
            k += 1
    jaro = (matches / len(a) + matches / len(b) + (matches - t / 2) / matches) / 3
    prefix = 0
    while prefix < min(4, len(a), len(b)) and a[prefix] == b[prefix]:
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def matches(query: str, candidate: str) -> bool:
    q, c = normalise(query).split(), normalise(candidate).split()
    if not q or not c:
        return False
    if q == c:
        return True
    total = 0.0
    for word in q:
        best = max(similarity(word, w) for w in c)
        if best < 0.8:
            return False
        total += best
    return total / len(q) - 0.01 * max(0, len(c) - len(q)) >= 0.86


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def status(store: Store, names: list[str]) -> list[dict]:
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    fresh = _iso(now - NEW_FOR)
    upcoming = [s for s in store.sessions(_iso(now), _iso(now + timedelta(days=400)))
                if (s["start_utc"] and s["start_utc"] > _iso(now)) or (not s["start_utc"] and (s["end_day"] or s["day"]) >= today)]
    grids = store.entries()
    lists = [r for r in store.entry_lists_with_events() if (r["end_day"] or r["start_day"] or "") >= today]
    out = []
    for name in names:
        found = []
        for series in sorted({g["series"] for g in grids}):
            hit = next((g for g in grids if g["series"] == series and (matches(name, g["name"]) or
                        (g["team"] and matches(name, g["team"])))), None)
            if hit:
                found.append({"series": series, "kind": "grid", "as": hit["name"] if matches(name, hit["name"]) else hit["team"],
                              "first_seen": hit["first_seen"] or ""})
        for r in lists:
            if matches(name, r["name"]):
                found.append({"series": r["series"], "kind": "entry", "as": r["name"], "event_key": r["event_key"],
                              "event_name": r["event_name"], "start_day": r["start_day"], "first_seen": r["first_seen"] or ""})
        # Next confirmed session: a grid series' next session, or the first session of an event they're entered in.
        candidates, pending = [], []
        for f in found:
            if f["kind"] == "grid" and f["series"] in PICK_EVENTS:
                entered = {x["series"] for x in found if x["kind"] == "entry"}
                s = next((s for s in upcoming if s["series"] == f["series"]), None)
                if s and f["series"] not in entered:
                    pending.append({"series": f["series"], "event_name": s["event_name"], "day": s["day"], "sort_utc": s["sort_utc"]})
                continue
            if f["kind"] == "grid":
                s = next((s for s in upcoming if s["series"] == f["series"]), None)
            else:
                s = next((s for s in upcoming if s["series"] == f["series"] and s["event_key"] == f["event_key"]), None)
            if s:
                candidates.append(s)
        nxt = min(candidates, key=lambda s: s["sort_utc"]) if candidates else None
        out.append({
            "name": name,
            "series": sorted({f["series"] for f in found}, key=lambda sid: list(BY_ID).index(sid)),
            "found": found,
            "next": {k: nxt[k] for k in ("series", "event_key", "event_name", "name", "kind", "start_utc", "day", "sort_utc")} if nxt else None,
            "new": [f for f in found if f["first_seen"] and f["first_seen"] >= fresh],
            # registered in a pick-your-events series, next event's entry list not out (or they're not on it yet)
            "pending": sorted(pending, key=lambda p: p["sort_utc"]),
        })
    return out
