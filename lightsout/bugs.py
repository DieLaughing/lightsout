"""Bug reports and ideas from inside the app, filed as GitHub issues.

Issues are anonymous: no name or email goes to GitHub, only the text and where it came from (page, device, app
version). Who sent what stays in this database so people can follow their own reports under Settings, which
shows the issue's state and the maintainer's latest reply.

Needs LIGHTSOUT_GITHUB_TOKEN: a fine-grained token with Issues: read and write on this one repo. Without it the
form hands people GitHub's own new-issue page instead (they need a GitHub account for that).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

log = logging.getLogger("lightsout.bugs")

REPO = os.environ.get("LIGHTSOUT_GITHUB_REPO", "DieLaughing/lightsout").strip()
TOKEN = os.environ.get("LIGHTSOUT_GITHUB_TOKEN", "").strip()
API = "https://api.github.com"
PER_DAY = 5                      # reports per person per 24 hours (the admin has no limit)
RECHECK = timedelta(minutes=10)  # how stale an issue's state may get before "Your reports" asks GitHub again
KINDS = {"bug": ("Bug", "bug"), "idea": ("Idea", "enhancement")}  # kind -> (word, GitHub label)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bug_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, created TEXT NOT NULL, kind TEXT NOT NULL,
    title TEXT NOT NULL, text TEXT NOT NULL, page TEXT, device TEXT,
    issue INTEGER, url TEXT, state TEXT, reply TEXT, reply_at TEXT, checked TEXT
);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()
_session: AsyncSession | None = None


def init(conn: sqlite3.Connection) -> None:
    global _conn
    _conn = conn
    with _lock, conn:
        conn.executescript(SCHEMA)


def configured() -> bool:
    return bool(TOKEN and REPO)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def title_for(text: str) -> str:
    first = " ".join(text.strip().splitlines()[0].split()) if text.strip() else "Report"
    return first if len(first) <= 70 else first[:69].rstrip() + "…"


def body_for(text: str, page: str, device: str, version: str) -> str:
    # Quote the text and break @mentions so a report can't ping people on GitHub.
    quoted = "\n".join("> " + line.replace("@", "@​") for line in text.strip().splitlines())
    rows = [("Page", f"`{page}`" if page else "–"), ("Device", device or "–"), ("App version", version or "–"),
            ("Sent", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))]
    table = "| | |\n|---|---|\n" + "\n".join(f"| {k} | {v.replace('|', '/')} |" for k, v in rows)
    return f"{quoted}\n\n{table}\n\n_Reported anonymously from inside the app._"


def new_issue_link(kind: str, text: str, page: str, device: str, version: str) -> str:
    """GitHub's own form, filled in: for installs without a token."""
    word, label = KINDS[kind]
    q = urlencode({"title": title_for(text), "body": body_for(text, page, device, version)[:6000], "labels": label})
    return f"https://github.com/{REPO}/issues/new?{q}"


def sent_today(email: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _conn.execute("SELECT COUNT(*) FROM bug_report WHERE email=? AND created>?", (email, since)).fetchone()[0]


async def _gh(method: str, path: str, **kw):
    global _session
    if _session is None:
        _session = AsyncSession(timeout=20)
    r = await _session.request(method, f"{API}{path}", headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lightsout-bug-reporter"}, **kw)
    r.raise_for_status()
    return r.json()


async def file(email: str, kind: str, text: str, page: str, device: str, version: str) -> dict:
    title = title_for(text)
    issue = await _gh("POST", f"/repos/{REPO}/issues", json={
        "title": title, "body": body_for(text, page, device, version), "labels": ["from-app", KINDS[kind][1], "needs-triage"]})
    with _lock, _conn:
        _conn.execute("INSERT INTO bug_report (email, created, kind, title, text, page, device, issue, url, state, checked)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (email, _now(), kind, title, text, page, device, issue["number"], issue["html_url"], "open", _now()))
    return {"issue": issue["number"], "url": issue["html_url"], "title": title}


async def _refresh(row: dict) -> None:
    issue = await _gh("GET", f"/repos/{REPO}/issues/{row['issue']}")
    state = "fixed" if issue.get("state_reason") == "completed" else issue.get("state", "open")
    reply, reply_at = row.get("reply"), row.get("reply_at")
    if issue.get("comments"):
        comments = await _gh("GET", f"/repos/{REPO}/issues/{row['issue']}/comments?per_page=100")
        mine = [c for c in comments if c.get("author_association") in ("OWNER", "MEMBER", "COLLABORATOR")]
        if mine:
            reply, reply_at = mine[-1]["body"][:800], mine[-1]["created_at"]
    with _lock, _conn:
        _conn.execute("UPDATE bug_report SET state=?, reply=?, reply_at=?, checked=? WHERE id=?",
                      (state, reply, reply_at, _now(), row["id"]))
    row.update(state=state, reply=reply, reply_at=reply_at)


async def mine(email: str) -> list[dict]:
    rows = [dict(r) for r in _conn.execute(
        "SELECT * FROM bug_report WHERE email=? ORDER BY created DESC LIMIT 20", (email,))]
    stale = datetime.now(timezone.utc) - RECHECK
    for row in rows:
        checked = datetime.fromisoformat((row["checked"] or "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
        if configured() and row["issue"] and checked < stale:
            try:
                await _refresh(row)
            except Exception as exc:
                log.warning("couldn't check issue #%s: %r", row["issue"], exc)
    return [{k: row[k] for k in ("created", "kind", "title", "issue", "url", "state", "reply", "reply_at")} for row in rows]
