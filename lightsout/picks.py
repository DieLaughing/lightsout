"""Pick'em for racing. Picking the winner out of 20-40 cars is a lottery, so each race has five Duels instead: two
drivers, pick who finishes ahead (1 point; one duel per race can be your lock, worth 2 if right and 0 if wrong). There's
also a Podium call: P1, P2 and P3, scoring 1 for each driver who ends up on the podium and 2 for the exact spot.

Duels are drawn up from the standings and the last race's field once a race is OPEN_DAYS away, then frozen so
everyone picks from the same list. Picks close when the race starts; friends' picks show from then. Results come from
the standings refresh the app already does (Last race), so nothing extra is asked of the series. One table per series
per season."""
from __future__ import annotations

import itertools
import json
import re
from datetime import datetime, timedelta, timezone

from .weather import norm

SERIES = ("f1", "cup", "indycar")
DUELS = 5
TEAMMATE_DUELS = 3                   # of the five, at most this many are teammate battles
OPEN_DAYS = 10                       # duels appear this long before the race
SHOW_AFTER = timedelta(days=2)       # the page keeps showing a race this long after its start, then moves on
REGRADE = timedelta(days=7)          # results are re-checked this long (NASCAR's official results can land a day late)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pick_race (
    series TEXT NOT NULL, event_key TEXT NOT NULL, season INTEGER NOT NULL, name TEXT NOT NULL, start TEXT NOT NULL,
    field TEXT NOT NULL,            -- JSON [{name, team, pos, points}]: who can be picked, frozen when drawn up
    duels TEXT NOT NULL,            -- JSON [[a, b], ...]
    result TEXT,                    -- JSON [{pos, name}] finishing order, once scored
    graded TEXT,
    PRIMARY KEY (series, event_key)
);
CREATE TABLE IF NOT EXISTS race_pick (
    email TEXT NOT NULL, series TEXT NOT NULL, event_key TEXT NOT NULL,
    kind TEXT NOT NULL,             -- duel | podium
    slot INTEGER NOT NULL,          -- duel number (0-4) or podium place (1-3)
    name TEXT NOT NULL, lock INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL,
    outcome TEXT,                   -- won | lost | void; NULL until scored
    points INTEGER,
    PRIMARY KEY (email, series, event_key, kind, slot)
);
CREATE INDEX IF NOT EXISTS race_pick_race ON race_pick (series, event_key);
"""


def init(conn) -> None:
    with conn:
        conn.executescript(SCHEMA)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def team_of(team: str) -> str:
    """NASCAR results carry "#19 · Joe Gibbs Racing"; the car number isn't part of the team."""
    return re.sub(r"^#\w+\s*·\s*", "", team or "").strip()


def finished(pos: str) -> bool:
    return str(pos).strip().isdigit()


# ---- races ------------------------------------------------------------------------------------
def races(store, series: str) -> list[dict]:
    """Every race (not sprint) of the series with a known start time, oldest first."""
    rows = store.conn.execute(
        "SELECT s.event_key, s.start_utc AS start, e.name, e.venue, e.season FROM session s "
        "JOIN event e ON e.series=s.series AND e.key=s.event_key "
        "WHERE s.series=? AND s.kind='race' AND s.start_utc IS NOT NULL ORDER BY s.start_utc", (series,))
    out, seen = [], set()
    for r in rows:
        if r["event_key"] not in seen:  # a double-header weekend counts once (its first race)
            seen.add(r["event_key"])
            out.append(dict(r))
    return out


def choose(all_races: list[dict], event_key: str | None, now: datetime | None = None) -> dict | None:
    """The race asked for, else the next one (a race stays up for SHOW_AFTER after it starts), else the last."""
    if event_key:
        return next((r for r in all_races if r["event_key"] == event_key), None)
    cut = _iso((now or _now()) - SHOW_AFTER)
    return next((r for r in all_races if r["start"] >= cut), all_races[-1] if all_races else None)


def field_from(st: dict | None) -> list[dict]:
    """Who's racing: the last race's finishers and retirements (so part-timers and old entries stay out), in
    championship order, with the team they drove for."""
    st = st or {}
    table = {r["name"]: r for r in st.get("drivers") or []}
    names = [(r["name"], team_of(r.get("team"))) for r in st.get("last_results") or []]
    if not names:
        names = [(r["name"], team_of(r.get("team"))) for r in st.get("drivers") or []]
    out = []
    for name, team in names:
        row = table.get(name) or {}
        out.append({"name": name, "team": team, "pos": row.get("pos") or 999, "points": row.get("points") or 0})
    return sorted(out, key=lambda d: (d["pos"], d["name"]))


def make_duels(field: list[dict], n: int = DUELS) -> list[list[str]]:
    """Close fights: up to TEAMMATE_DUELS teammate battles, then drivers near each other in the championship.
    Closest on points first; battles at the back of the field count as less close. Nobody appears twice."""
    rank = {d["name"]: i for i, d in enumerate(field)}
    half = max(len(field) // 2, 6)
    top = max((d["points"] for d in field), default=0) or 1

    def cost(a: dict, b: dict) -> float:
        c = abs(a["points"] - b["points"]) / top
        return c + (0.5 if rank[a["name"]] >= half and rank[b["name"]] >= half else 0)

    teams: dict[str, list[dict]] = {}
    for d in field:
        if d["team"]:
            teams.setdefault(d["team"], []).append(d)
    mates = [(cost(a, b), a, b) for ds in teams.values() for a, b in itertools.combinations(ds, 2)]
    neighbours = [(cost(a, b), a, b) for a, b in [*zip(field, field[1:]), *zip(field, field[2:])]]  # up to 2 places apart
    used: set[str] = set()
    out: list[list[str]] = []

    def take(pairs: list, limit: int) -> None:
        for _, a, b in sorted(pairs, key=lambda p: (p[0], min(rank[p[1]["name"]], rank[p[2]["name"]]))):
            if len(out) >= limit:
                return
            if a["name"] in used or b["name"] in used:
                continue
            used.update((a["name"], b["name"]))
            out.append(sorted([a["name"], b["name"]], key=rank.get))

    take(mates, TEAMMATE_DUELS)
    take([p for p in neighbours if p[1]["team"] != p[2]["team"]], n)
    take(neighbours + mates, n)  # a short field can still fill up with teammates
    return sorted(out, key=lambda p: rank[p[0]])


def slate(store, series: str, race: dict, now: datetime | None = None) -> dict | None:
    """The race's frozen duels and field, drawing them up when the race is close enough. None = not open yet."""
    now = now or _now()
    row = store.conn.execute("SELECT * FROM pick_race WHERE series=? AND event_key=?", (series, race["event_key"])).fetchone()
    if row:
        if row["start"] != race["start"] or row["name"] != race["name"]:  # rescheduled or renamed: picks follow
            with store.lock, store.conn:
                store.conn.execute("UPDATE pick_race SET start=?, name=? WHERE series=? AND event_key=?",
                                   (race["start"], race["name"], series, race["event_key"]))
            row = dict(row) | {"start": race["start"], "name": race["name"]}
        return _decode(row)
    start = _parse(race["start"])
    if now >= start or start - now > timedelta(days=OPEN_DAYS):
        return None
    field = field_from(store.standings(series))
    duels = make_duels(field)
    if not duels:
        return None
    with store.lock, store.conn:
        store.conn.execute("INSERT OR IGNORE INTO pick_race (series, event_key, season, name, start, field, duels) "
                           "VALUES (?,?,?,?,?,?,?)", (series, race["event_key"], race["season"], race["name"],
                                                     race["start"], json.dumps(field), json.dumps(duels)))
    return slate(store, series, race, now)


def _decode(row) -> dict:
    d = dict(row)
    d["field"], d["duels"] = json.loads(d["field"]), json.loads(d["duels"])
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    return d


# ---- picking ----------------------------------------------------------------------------------
def set_pick(store, email: str, sl: dict, kind: str, slot: int, name: str | None, lock: bool = False,
             now: datetime | None = None) -> None:
    """Saves (or with name=None clears) one pick. ValueError with a message people can read when it's not allowed."""
    if (now or _now()) >= _parse(sl["start"]):
        raise ValueError("Picks closed when the race started.")
    key = (email, sl["series"], sl["event_key"])
    with store.lock, store.conn:
        c = store.conn
        if name is None:
            c.execute("DELETE FROM race_pick WHERE email=? AND series=? AND event_key=? AND kind=? AND slot=?", (*key, kind, slot))
            return
        if kind == "duel":
            if not 0 <= slot < len(sl["duels"]) or name not in sl["duels"][slot]:
                raise ValueError("Pick one of the two drivers.")
            if lock:
                c.execute("UPDATE race_pick SET lock=0 WHERE email=? AND series=? AND event_key=? AND kind='duel'", key)
        elif kind == "podium":
            if slot not in (1, 2, 3) or name not in {d["name"] for d in sl["field"]}:
                raise ValueError("Pick a driver from this race.")
            lock = False
            # The same driver can't be on your podium twice: picking them for a new spot moves them there.
            c.execute("DELETE FROM race_pick WHERE email=? AND series=? AND event_key=? AND kind='podium' AND name=? AND slot<>?",
                      (*key, name, slot))
        else:
            raise ValueError("Unknown pick.")
        c.execute("""INSERT INTO race_pick (email, series, event_key, kind, slot, name, lock, created) VALUES (?,?,?,?,?,?,?,?)
                     ON CONFLICT (email, series, event_key, kind, slot) DO UPDATE SET name=excluded.name, lock=excluded.lock""",
                  (*key, kind, slot, name, int(bool(lock)), _iso(now or _now())))


# ---- scoring ----------------------------------------------------------------------------------
def same_race(last_event: str, race: dict) -> bool:
    """Does the standings' Last race belong to this race? Names differ a little between sources
    ("Bahrain Grand Prix" vs "Bahrain Grand Prix in Malaysia"), so either containing the other counts."""
    n = norm(last_event)
    if not n:
        return False
    return any(x and (n == x or n in x or x in n) for x in (norm(race.get("name")), norm(race.get("venue") or "")))


def duel_outcome(order: list[dict], pair: list[str], picked: str) -> str:
    where = {r["name"]: i for i, r in enumerate(order)}
    a, b = pair
    if a not in where or b not in where:
        return "void"            # one of them didn't race
    by = {r["name"]: r for r in order}
    if not finished(by[a]["pos"]) and not finished(by[b]["pos"]):
        return "void"            # both out: no winner
    ahead = a if where[a] < where[b] else b
    return "won" if picked == ahead else "lost"


def podium_points(order: list[dict], slot: int, picked: str) -> int:
    top = [r["name"] for r in order if finished(r["pos"])][:3]
    if len(top) >= slot and top[slot - 1] == picked:
        return 2
    return 1 if picked in top else 0


def grade(store, series: str, now: datetime | None = None) -> int:
    """Scores this series' recent races from the saved Last race. Re-run freely (it rewrites the same answers, and
    picks up corrections such as NASCAR's official results replacing the unofficial ones). Returns picks scored."""
    if series not in SERIES:
        return 0
    st = store.standings(series) or {}
    order = st.get("last_results") or []
    if not order or not st.get("last_event"):
        return 0
    now = now or _now()
    rows = store.conn.execute("SELECT * FROM pick_race WHERE series=? AND start<=? AND start>=?",
                              (series, _iso(now), _iso(now - REGRADE))).fetchall()
    n = 0
    for row in rows:
        race = dict(row)
        venue = store.conn.execute("SELECT venue FROM event WHERE series=? AND key=?", (series, race["event_key"])).fetchone()
        race["venue"] = venue[0] if venue else ""
        # The standings must have been saved after the race started, or it's last weekend's result with a similar name.
        if not same_race(st["last_event"], race) or (st.get("updated") or "") < race["start"]:
            continue
        duels = json.loads(race["duels"])
        result = json.dumps([{"pos": r["pos"], "name": r["name"]} for r in order])
        with store.lock, store.conn:
            c = store.conn
            for p in c.execute("SELECT * FROM race_pick WHERE series=? AND event_key=?", (series, race["event_key"])).fetchall():
                if p["kind"] == "duel":
                    if p["slot"] >= len(duels):
                        continue
                    outcome = duel_outcome(order, duels[p["slot"]], p["name"])
                    points = (2 if p["lock"] else 1) if outcome == "won" else 0
                else:
                    points = podium_points(order, p["slot"], p["name"])
                    outcome = "won" if points else "lost"
                c.execute("UPDATE race_pick SET outcome=?, points=? WHERE email=? AND series=? AND event_key=? AND kind=? AND slot=?",
                          (outcome, points, p["email"], series, race["event_key"], p["kind"], p["slot"]))
                n += 1
            c.execute("UPDATE pick_race SET result=?, graded=? WHERE series=? AND event_key=?",
                      (result, _iso(now), series, race["event_key"]))
    return n


# ---- what the page shows ----------------------------------------------------------------------
def view(store, email: str, sl: dict, names: dict[str, str], now: datetime | None = None) -> dict:
    """The slate with this person's picks, and everyone's once the race has started."""
    started = (now or _now()) >= _parse(sl["start"])
    rows = [dict(r) for r in store.conn.execute(
        "SELECT * FROM race_pick WHERE series=? AND event_key=? ORDER BY created", (sl["series"], sl["event_key"]))]
    rows = [r for r in rows if r["email"] in names]
    info = {d["name"]: d for d in sl["field"]}
    order = sl["result"] or []
    place = {r["name"]: r["pos"] for r in order}

    def person(r: dict) -> dict:
        return {"name": names[r["email"]], "me": r["email"] == email, "pick": r["name"], "lock": bool(r["lock"]),
                "outcome": r["outcome"], "points": r["points"]}

    duels = []
    for i, pair in enumerate(sl["duels"]):
        picks = [r for r in rows if r["kind"] == "duel" and r["slot"] == i]
        mine = next((r for r in picks if r["email"] == email), None)
        duels.append({
            "slot": i,
            "drivers": [{"name": n, "team": (info.get(n) or {}).get("team", ""), "pos": (info.get(n) or {}).get("pos"),
                         "finish": place.get(n)} for n in pair],
            "mine": mine["name"] if mine else None, "lock": bool(mine and mine["lock"]),
            "outcome": mine["outcome"] if mine else None, "points": mine["points"] if mine else None,
            "count": len(picks),
            "everyone": [person(r) for r in picks if r["email"] != email] if started else None,
        })
    pod = [r for r in rows if r["kind"] == "podium"]
    mine_pod = {r["slot"]: r for r in pod if r["email"] == email}
    friends: dict[str, list] = {}
    for r in pod:
        if r["email"] != email:
            friends.setdefault(r["email"], [None, None, None])[r["slot"] - 1] = r["name"]
    return {
        "started": started, "graded": sl.get("graded"),
        "duels": duels,
        "podium": {"mine": [(mine_pod.get(s) or {}).get("name") for s in (1, 2, 3)],
                   "points": [(mine_pod.get(s) or {}).get("points") for s in (1, 2, 3)],
                   "actual": [r["name"] for r in order if finished(r["pos"])][:3] if order else None,
                   "count": len({r["email"] for r in pod if r["email"] != email}),
                   "everyone": [{"name": names[e], "picks": p} for e, p in friends.items()] if started else None},
        "field": [{"name": d["name"], "team": d["team"]} for d in sl["field"]],
    }


def board(store, series: str, season: int, names: dict[str, str], email: str) -> dict:
    """The season table for one series, plus the last scored race."""
    rows = [dict(r) for r in store.conn.execute(
        "SELECT p.email, p.event_key, p.kind, p.outcome, p.points, r.start, r.name AS race FROM race_pick p "
        "JOIN pick_race r ON r.series=p.series AND r.event_key=p.event_key "
        "WHERE p.series=? AND r.season=? AND p.outcome IS NOT NULL", (series, season))]
    rows = [r for r in rows if r["email"] in names]

    def table(picked: list[dict]) -> list[dict]:
        people: dict[str, dict] = {}
        for r in picked:
            p = people.setdefault(r["email"], {"name": names[r["email"]], "me": r["email"] == email,
                                               "pts": 0, "w": 0, "l": 0, "podium": 0})
            p["pts"] += r["points"] or 0
            if r["kind"] == "podium":
                p["podium"] += r["points"] or 0
            elif r["outcome"] in ("won", "lost"):
                p["w" if r["outcome"] == "won" else "l"] += 1
        return sorted(people.values(), key=lambda p: (-p["pts"], -p["w"], p["l"], p["name"].lower()))

    last = max(rows, key=lambda r: r["start"], default=None)
    return {"season": season, "table": table(rows),
            "last": {"race": last["race"], "event_key": last["event_key"],
                     "table": table([r for r in rows if r["event_key"] == last["event_key"]])} if last else None}
