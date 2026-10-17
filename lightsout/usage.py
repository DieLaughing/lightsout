"""Page views, counted per page and day: which parts of the app people actually open. Anonymous on purpose: a row is
(day, page pattern, views), never who. The owner's own views aren't counted. Kept KEEP_DAYS, shown on /admin.

Pages are client-side #/ routes, so the phone counts its own navigation and sends a batch now and then (POST /api/views);
counting can never slow a page down."""
from __future__ import annotations

import sqlite3
import threading
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS page_view (
    day TEXT NOT NULL, route TEXT NOT NULL, views INTEGER NOT NULL, PRIMARY KEY (day, route)
);
"""

KEEP_DAYS = 180
ROUTES = frozenset({"home", "wire", "new", "app", "series", "settings", "standings", "picks", "report", "series/:id",
                    "live/:series", "driver/:name", "team/:name", "story/:id", "event/:id", "track/:id",
                    "standings/:series", "picks/:sub"})   # what countView() in web/app.js sends
MAX_ROUTES = 30      # per batch
MAX_VIEWS = 200      # per route per batch
PER_DAY = 3000       # per person per day, held in memory only: one phone (or a stuck loop) can't flood the counts
EASTERN = ZoneInfo("America/New_York")

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()
_cleaned: str | None = None   # day of the last cleanup
_sent: dict[str, int] = {}    # who -> views counted today (memory only, never saved); reset with the day


def init(conn: sqlite3.Connection) -> None:
    """`conn` is usage's own connection (the app opens one to its database file), so these commits never land in
    the middle of another module's transaction."""
    global _conn
    _conn = conn
    with _lock, conn:
        conn.executescript(SCHEMA)


def app_day(now: datetime | None = None) -> str:
    """The day views count for: US Eastern, with late-night sessions until 6 am counting for the day before
    (the same rule as Shots on Goal's slate)."""
    return ((now or datetime.now(EASTERN)).astimezone(EASTERN) - timedelta(hours=6)).date().isoformat()


def _clean(day: str) -> None:
    """Once a day: drop rows past KEEP_DAYS and start the per-person allowance over. Caller holds _lock."""
    global _cleaned
    if _cleaned == day:
        return
    with _conn:
        _conn.execute("DELETE FROM page_view WHERE day < ?", ((Date.fromisoformat(day) - timedelta(days=KEEP_DAYS)).isoformat(),))
    _sent.clear()
    _cleaned = day


def add(views: dict, who: str, day: str | None = None) -> int:
    """Add one batch from a phone ({route: count}) of pages in ROUTES to app_day() (last night stays until 6 am).
    Bad keys and counts are dropped; `who` only meters PER_DAY. Returns the views counted."""
    day = day or app_day()
    rows = [(day, r, min(n, MAX_VIEWS)) for r, n in views.items()
            if r in ROUTES and isinstance(n, int) and not isinstance(n, bool) and n > 0][:MAX_ROUTES]
    with _lock:
        _clean(day)
        room, counted = PER_DAY - _sent.get(who, 0), []
        for d, r, n in rows:
            n = min(n, room)
            if n > 0:
                counted.append((d, r, n))
                room -= n
        if counted:
            with _conn:
                _conn.executemany("INSERT INTO page_view (day, route, views) VALUES (?,?,?) "
                                  "ON CONFLICT (day, route) DO UPDATE SET views = views + excluded.views", counted)
        _sent[who] = PER_DAY - room
    return sum(n for _, _, n in counted)


def days(since: str | None = None) -> list[dict]:
    """Every kept row (or from `since`), oldest first: the raw counts for /api/admin/usage."""
    with _lock:
        _clean(app_day())
        cur = _conn.execute("SELECT day, route, views FROM page_view WHERE day >= ? ORDER BY day, route", (since or "",))
        return [{"day": d, "route": r, "views": v} for d, r, v in cur]


def summary(day: str | None = None) -> dict:
    """Per page: views in the last 7 and 30 days and everything kept, busiest first."""
    day = day or app_day()
    with _lock:
        _clean(day)
        end = Date.fromisoformat(day)
        d7, d30 = (end - timedelta(days=6)).isoformat(), (end - timedelta(days=29)).isoformat()
        cur = _conn.execute("SELECT route, SUM(CASE WHEN day >= ? THEN views ELSE 0 END), "
                            "SUM(CASE WHEN day >= ? THEN views ELSE 0 END), SUM(views) "
                            "FROM page_view GROUP BY route", (d7, d30))
        pages = sorted(({"route": r, "week": w, "month": m, "all": a} for r, w, m, a in cur),
                       key=lambda p: (-p["month"], -p["all"], p["route"]))
        return {"pages": pages, "first": _conn.execute("SELECT MIN(day) FROM page_view").fetchone()[0]}
