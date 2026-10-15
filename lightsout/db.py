"""SQLite storage. One small file; adapters replace whole seasons at a time."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import AdapterResult, Standings

SCHEMA = """
CREATE TABLE IF NOT EXISTS event (
    series TEXT NOT NULL, key TEXT NOT NULL, season INTEGER NOT NULL,
    name TEXT NOT NULL, venue TEXT, country TEXT, start_day TEXT, end_day TEXT, url TEXT,
    PRIMARY KEY (series, key)
);
CREATE TABLE IF NOT EXISTS session (
    series TEXT NOT NULL, event_key TEXT NOT NULL, key TEXT NOT NULL,
    name TEXT NOT NULL, kind TEXT NOT NULL,
    start_utc TEXT, end_utc TEXT,   -- set when the time is known
    day TEXT, end_day TEXT,         -- local dates; always set
    sort_utc TEXT NOT NULL,         -- start_utc, or noon UTC on `day` for TBA sessions
    tv TEXT,                        -- per-session network, when the source publishes one
    PRIMARY KEY (series, event_key, key)
);
CREATE INDEX IF NOT EXISTS session_sort ON session (sort_utc);
CREATE TABLE IF NOT EXISTS entry (
    series TEXT NOT NULL, name TEXT NOT NULL, team TEXT, number TEXT, first_seen TEXT,
    PRIMARY KEY (series, name)
);
CREATE TABLE IF NOT EXISTS event_entry (
    series TEXT NOT NULL, event_key TEXT NOT NULL, name TEXT NOT NULL, first_seen TEXT,
    PRIMARY KEY (series, event_key, name)
);
CREATE TABLE IF NOT EXISTS standings (
    series TEXT PRIMARY KEY, data TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS round_result (   -- each race's classification, for season form on the driver cards
    series TEXT NOT NULL, season INTEGER NOT NULL, event TEXT NOT NULL,
    day TEXT NOT NULL,                      -- race date (UTC), orders the rounds
    data TEXT NOT NULL,                     -- {name: [pos text, points]}
    PRIMARY KEY (series, season, event)
);
CREATE TABLE IF NOT EXISTS user (
    email TEXT PRIMARY KEY,
    status TEXT NOT NULL,          -- pending | approved | denied | revoked
    requested TEXT, decided TEXT,
    nonce TEXT                     -- makes each Approve/Deny link single-use
);
CREATE TABLE IF NOT EXISTS invite (   -- used_by/used_at: single-use invites from before invite_use
    code TEXT PRIMARY KEY, created TEXT NOT NULL, note TEXT, used_by TEXT, used_at TEXT,
    max_uses INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS invite_use (
    code TEXT NOT NULL, email TEXT NOT NULL, used_at TEXT NOT NULL, PRIMARY KEY (code, email)
);
CREATE TABLE IF NOT EXISTS race_event (   -- incidents, penalties, cautions, retirements... from live feeds
    id TEXT PRIMARY KEY,                      -- stable per feed item, so a reconnect never duplicates
    series TEXT NOT NULL, event_key TEXT, session TEXT,
    time TEXT NOT NULL, lap INTEGER, kind TEXT NOT NULL, important INTEGER NOT NULL,
    text TEXT NOT NULL, drivers TEXT NOT NULL DEFAULT '[]', teams TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS race_event_time ON race_event (time);
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY, time TEXT NOT NULL, title TEXT NOT NULL, text TEXT, url TEXT NOT NULL, source TEXT,
    series TEXT NOT NULL DEFAULT '[]', drivers TEXT NOT NULL DEFAULT '[]', teams TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS news_time ON news (time);
CREATE TABLE IF NOT EXISTS push_sub (
    endpoint TEXT PRIMARY KEY, email TEXT NOT NULL, sub TEXT NOT NULL, created TEXT NOT NULL, agent TEXT
);
CREATE TABLE IF NOT EXISTS sent (        -- alerts already pushed (or deliberately skipped), so none repeat
    key TEXT PRIMARY KEY, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adapter_run (
    series TEXT PRIMARY KEY, last_try TEXT, last_ok TEXT, error TEXT, events INTEGER, sessions INTEGER
);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()  # request handlers run in threads; serialise the access writes
        self.conn.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL: a save doesn't wait for the disk to confirm it (on a USB drive that's up to ~400 ms, and saves
        # run on the worker that answers requests). A power cut can lose the last moment of saves; nothing corrupts.
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        # Columns added after the first deploy: (table, column).
        for table, column in (("entry", "first_seen"), ("event_entry", "first_seen"), ("session", "tv"),
                              ("user", "settings")):
            cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")]
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        if "max_uses" not in [r[1] for r in self.conn.execute("PRAGMA table_info(invite)")]:  # before multi-use invites
            with self.conn:
                self.conn.execute("ALTER TABLE invite ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1")
                self.conn.execute("INSERT OR IGNORE INTO invite_use (code, email, used_at) "
                                  "SELECT code, used_by, used_at FROM invite WHERE used_by IS NOT NULL")

    # ---- checkpoints off the request path ------------------------------------------------------
    def manual_checkpoints(self) -> None:
        """Stop SQLite folding the WAL back into the database inside whichever save triggers it (on a USB drive that
        save could wait hundreds of ms). The refresh loop calls checkpoint() between passes instead."""
        self.conn.execute("PRAGMA wal_autocheckpoint=0")

    def checkpoint(self) -> tuple:
        """A PASSIVE checkpoint on its own connection: never blocks readers or writers. Run it in a thread."""
        if getattr(self, "_ckpt", None) is None:
            self._ckpt = sqlite3.connect(self.path, check_same_thread=False)
        return tuple(self._ckpt.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())

    def save(self, series: str, result: AdapterResult) -> tuple[int, int]:
        """Replace every event of the covered seasons for this series."""
        n_sessions = 0
        now = _iso(datetime.now(timezone.utc))
        with self.conn:
            for season in result.seasons:
                keys = [r[0] for r in self.conn.execute(
                    "SELECT key FROM event WHERE series=? AND season=?", (series, season))]
                self.conn.executemany("DELETE FROM session WHERE series=? AND event_key=?",
                                      [(series, k) for k in keys])
                self.conn.execute("DELETE FROM event WHERE series=? AND season=?", (series, season))
            for ev in result.events:
                ev.normalised()
                self.conn.execute(
                    "INSERT OR REPLACE INTO event VALUES (?,?,?,?,?,?,?,?,?)",
                    (series, ev.key, ev.season, ev.name, ev.venue, ev.country,
                     ev.start_day.isoformat() if ev.start_day else None,
                     ev.end_day.isoformat() if ev.end_day else None, ev.url))
                if ev.entrants is not None:
                    # Keep when each name first appeared, so "newly entered" survives refreshes.
                    # The very first import of a series is the baseline (first_seen empty = not news).
                    seen = dict(self.conn.execute("SELECT name, first_seen FROM event_entry WHERE series=? AND event_key=?",
                                                  (series, ev.key)).fetchall())
                    baseline = not self.conn.execute("SELECT 1 FROM event_entry WHERE series=? LIMIT 1", (series,)).fetchone()
                    self.conn.execute("DELETE FROM event_entry WHERE series=? AND event_key=?", (series, ev.key))
                    self.conn.executemany("INSERT OR IGNORE INTO event_entry VALUES (?,?,?,?)",
                                          [(series, ev.key, n, seen.get(n, "" if baseline else now)) for n in ev.entrants])
                for s in ev.sessions:
                    sort = _iso(s.start) or f"{s.day.isoformat()}T12:00:00Z"
                    self.conn.execute(
                        "INSERT OR REPLACE INTO session (series, event_key, key, name, kind, start_utc, end_utc, day, end_day, "
                        "sort_utc, tv) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (series, ev.key, s.key, s.name, s.kind, _iso(s.start), _iso(s.end),
                         s.day.isoformat() if s.day else None,
                         s.end_day.isoformat() if s.end_day else None, sort, s.tv or None))
                    n_sessions += 1
            if result.entries:
                seen = dict(self.conn.execute("SELECT name, first_seen FROM entry WHERE series=?", (series,)).fetchall())
                baseline = not seen
                self.conn.execute("DELETE FROM entry WHERE series=?", (series,))
                self.conn.executemany("INSERT OR REPLACE INTO entry VALUES (?,?,?,?,?)",
                                      [(series, e.name, e.team, e.number, seen.get(e.name, "" if baseline else now))
                                       for e in result.entries])
        return len(result.events), n_sessions

    def record_run(self, series: str, ok: bool, error: str | None = None, counts: tuple[int, int] = (0, 0)):
        now = _iso(datetime.now(timezone.utc))
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO adapter_run (series) VALUES (?)", (series,))
            if ok:
                self.conn.execute("UPDATE adapter_run SET last_try=?, last_ok=?, error=NULL, events=?, sessions=? "
                                  "WHERE series=?", (now, now, *counts, series))
            else:
                self.conn.execute("UPDATE adapter_run SET last_try=?, error=? WHERE series=?", (now, error, series))

    def runs(self) -> dict[str, dict]:
        return {r["series"]: dict(r) for r in self.conn.execute("SELECT * FROM adapter_run")}

    def sessions(self, start: str, end: str, series: list[str] | None = None) -> list[dict]:
        """Sessions overlapping [start, end) in UTC ISO, joined with their event."""
        q = ("SELECT s.*, e.name AS event_name, e.venue, e.country, e.url, e.season, "
             "e.start_day AS event_start, e.end_day AS event_end "
             "FROM session s JOIN event e ON e.series=s.series AND e.key=s.event_key "
             "WHERE s.sort_utc < ? AND COALESCE(s.end_utc, (COALESCE(s.end_day, s.day) || 'T23:59:59Z')) >= ?")
        args: list = [end, start]
        if series:
            q += f" AND s.series IN ({','.join('?' * len(series))})"
            args += series
        return [dict(r) for r in self.conn.execute(q + " ORDER BY s.sort_utc, s.series", args)]

    def events(self, series: str) -> list[dict]:
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM event WHERE series=? ORDER BY start_day", (series,))]
        for r in rows:
            r["sessions"] = [dict(s) for s in self.conn.execute(
                "SELECT * FROM session WHERE series=? AND event_key=? ORDER BY sort_utc", (series, r["key"]))]
        return rows

    def entries(self, series: str | None = None) -> list[dict]:
        if series:
            return [dict(r) for r in self.conn.execute("SELECT * FROM entry WHERE series=? ORDER BY name", (series,))]
        return [dict(r) for r in self.conn.execute("SELECT * FROM entry ORDER BY series, name")]

    def save_standings(self, series: str, st: Standings) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO standings VALUES (?,?,?)",
                              (series, json.dumps(asdict(st)), _iso(datetime.now(timezone.utc))))

    def standings(self, series: str) -> dict | None:
        row = self.conn.execute("SELECT data, updated FROM standings WHERE series=?", (series,)).fetchone()
        return {**json.loads(row["data"]), "updated": row["updated"]} if row else None

    def save_round(self, series: str, season: int, event: str, day: str, rows: dict[str, list]) -> None:
        """One race's classification. Saving again (official results after unofficial ones) replaces it."""
        if not event or not rows:
            return
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO round_result VALUES (?,?,?,COALESCE((SELECT day FROM round_result "
                              "WHERE series=? AND season=? AND event=?), ?),?)",
                              (series, season, event, series, season, event, day, json.dumps(rows)))

    def rounds(self, series: str, season: int) -> list[dict]:
        return [{"event": r["event"], "day": r["day"], "rows": json.loads(r["data"])} for r in self.conn.execute(
            "SELECT event, day, data FROM round_result WHERE series=? AND season=? ORDER BY day", (series, season))]

    def round_events(self, series: str, season: int) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT event FROM round_result WHERE series=? AND season=?", (series, season))}

    def entry_lists_with_events(self) -> list[dict]:
        """Every published entry-list name with its event, for the follow search."""
        return [dict(r) for r in self.conn.execute(
            "SELECT x.series, x.event_key, x.name, x.first_seen, e.name AS event_name, e.start_day, e.end_day "
            "FROM event_entry x JOIN event e ON e.series=x.series AND e.key=x.event_key")]

    # ---- people (access is approved per email; Cloudflare only proves who someone is) ----
    def user(self, email: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM user WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None

    def users(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM user ORDER BY status, requested DESC")]

    def set_status(self, email: str, status: str, nonce: str | None = None) -> None:
        now = _iso(datetime.now(timezone.utc))
        with self.lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO user (email, status) VALUES (?, ?)", (email, status))
            if status == "pending":
                self.conn.execute("UPDATE user SET status=?, requested=?, decided=NULL, nonce=? WHERE email=?",
                                  (status, now, nonce, email))
            else:
                self.conn.execute("UPDATE user SET status=?, decided=?, nonce=NULL WHERE email=?",
                                  (status, now, email))

    def settings(self, email: str) -> dict | None:
        """A person's saved settings, or None if they've never saved any (the browser's copy then moves in)."""
        row = self.conn.execute("SELECT settings FROM user WHERE email=?", (email,)).fetchone()
        return json.loads(row["settings"]) if row and row["settings"] else None

    def save_settings(self, email: str, settings: dict) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE user SET settings=? WHERE email=?", (json.dumps(settings), email))

    def approved_settings(self) -> dict[str, dict]:
        rows = self.conn.execute("SELECT email, settings FROM user WHERE status='approved'")
        return {r["email"]: json.loads(r["settings"]) if r["settings"] else {} for r in rows}

    # ---- race events -----------------------------------------------------------------
    def add_events(self, events: list[dict]) -> list[dict]:
        """Store new events; returns the ones that weren't already there."""
        fresh = []
        with self.lock, self.conn:
            for e in events:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO race_event VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (e["id"], e["series"], e.get("event_key"), e.get("session"), e["time"], e.get("lap"), e["kind"],
                     int(bool(e.get("important"))), e["text"], json.dumps(e.get("drivers") or []), json.dumps(e.get("teams") or [])))
                if cur.rowcount:
                    fresh.append(e)
        return fresh

    def events_since(self, since: str, series: list[str] | None = None, important: bool | None = None,
                     event_key: str | None = None, limit: int = 500) -> list[dict]:
        q, args = "SELECT * FROM race_event WHERE time >= ?", [since]
        if series:
            q += f" AND series IN ({','.join('?' * len(series))})"
            args += series
        if important is not None:
            q += " AND important=?"
            args.append(int(important))
        if event_key:
            q += " AND event_key=?"
            args.append(event_key)
        rows = self.conn.execute(q + " ORDER BY time DESC LIMIT ?", (*args, limit))
        return [{**dict(r), "important": bool(r["important"]), "drivers": json.loads(r["drivers"]),
                 "teams": json.loads(r["teams"])} for r in rows]

    # ---- news ----------------------------------------------------------------------------
    def add_news(self, items: list[dict]) -> list[dict]:
        fresh = []
        with self.lock, self.conn:
            for n in items:
                cur = self.conn.execute("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?,?,?)",
                                        (n["id"], n["time"], n["title"], n.get("text"), n["url"], n.get("source"),
                                         json.dumps(n["series"]), json.dumps(n["drivers"]), json.dumps(n["teams"])))
                if cur.rowcount:
                    fresh.append(n)
            self.conn.execute("DELETE FROM news WHERE time < ?", (_iso(datetime.now(timezone.utc) - timedelta(days=30)),))
        return fresh

    def news(self, limit: int = 400) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM news ORDER BY time DESC LIMIT ?", (limit,))
        return [{**dict(r), **{k: json.loads(r[k]) for k in ("series", "drivers", "teams")}} for r in rows]

    # ---- push subscriptions and sent alerts ----------------------------------------------
    def add_push(self, email: str, sub: dict, agent: str | None) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO push_sub VALUES (?,?,?,?,?)",
                              (sub["endpoint"], email, json.dumps(sub), _iso(datetime.now(timezone.utc)), (agent or "")[:200]))

    def remove_push(self, endpoint: str, email: str | None = None) -> None:
        with self.lock, self.conn:
            if email:
                self.conn.execute("DELETE FROM push_sub WHERE endpoint=? AND email=?", (endpoint, email))
            else:
                self.conn.execute("DELETE FROM push_sub WHERE endpoint=?", (endpoint,))

    def pushes(self, email: str | None = None) -> list[dict]:
        """Subscriptions of approved people (one person, or everyone)."""
        q = "SELECT p.* FROM push_sub p JOIN user u ON u.email=p.email WHERE u.status='approved'"
        rows = self.conn.execute(q + (" AND p.email=?" if email else ""), (email,) if email else ())
        return [{**dict(r), "sub": json.loads(r["sub"])} for r in rows]

    def was_sent(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sent WHERE key=?", (key,)).fetchone() is not None

    def mark_sent(self, keys: list[str]) -> None:
        now = _iso(datetime.now(timezone.utc))
        with self.lock, self.conn:
            self.conn.executemany("INSERT OR IGNORE INTO sent VALUES (?,?)", [(k, now) for k in keys])
            self.conn.execute("DELETE FROM sent WHERE at < ?", (_iso(datetime.now(timezone.utc) - timedelta(days=40)),))

    def add_invite(self, code: str, note: str | None, max_uses: int = 1) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO invite (code, created, note, max_uses) VALUES (?,?,?,?)",
                              (code, _iso(datetime.now(timezone.utc)), note, max_uses))

    def invite(self, code: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM invite WHERE code=?", (code,)).fetchone()
        if not row:
            return None
        uses = [dict(r) for r in self.conn.execute(
            "SELECT email, used_at FROM invite_use WHERE code=? ORDER BY used_at", (code,))]
        return {k: row[k] for k in ("code", "created", "note", "max_uses")} | {"uses": uses}

    def use_invite(self, code: str, email: str) -> bool:
        """Records this email on an invite with room left. False if it doesn't exist, is full, or they already used it."""
        with self.lock, self.conn:
            row = self.conn.execute("SELECT max_uses FROM invite WHERE code=?", (code,)).fetchone()
            if not row:
                return False
            used = self.conn.execute("SELECT COUNT(*) FROM invite_use WHERE code=?", (code,)).fetchone()[0]
            if used >= row["max_uses"]:
                return False
            cur = self.conn.execute("INSERT OR IGNORE INTO invite_use (code, email, used_at) VALUES (?,?,?)",
                                    (code, email, _iso(datetime.now(timezone.utc))))
            return cur.rowcount == 1

    def invites(self) -> list[dict]:
        codes = [r[0] for r in self.conn.execute("SELECT code FROM invite ORDER BY created DESC")]
        return [self.invite(c) for c in codes]

    def delete_invite(self, code: str) -> None:
        """Deletes an unused invite; one somebody joined with is closed instead, so it still shows who used it."""
        with self.lock, self.conn:
            used = self.conn.execute("SELECT COUNT(*) FROM invite_use WHERE code=?", (code,)).fetchone()[0]
            if used:
                self.conn.execute("UPDATE invite SET max_uses=? WHERE code=?", (used, code))
            else:
                self.conn.execute("DELETE FROM invite WHERE code=?", (code,))
