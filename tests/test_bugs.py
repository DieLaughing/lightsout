"""Bug reports: anonymous GitHub issues, rate limits and "Your reports" (GitHub is faked)."""
import asyncio
import sqlite3

from lightsout import bugs


def _setup(monkeypatch):
    bugs.init(sqlite3.connect(":memory:", check_same_thread=False))
    bugs._conn.row_factory = sqlite3.Row
    monkeypatch.setattr(bugs, "TOKEN", "t")
    calls = []
    issue = {"number": 7, "html_url": "https://github.com/x/y/issues/7", "state": "open", "comments": 0}

    async def fake(method, path, **kw):
        calls.append((method, path, kw.get("json")))
        if method == "POST":
            return issue
        if path.endswith("/comments?per_page=100"):
            return [{"author_association": "NONE", "body": "me too", "created_at": "x"},
                    {"author_association": "OWNER", "body": "Fixed in today's update.", "created_at": "2026-10-14T01:00:00Z"}]
        return {**issue, "state": "closed", "state_reason": "completed", "comments": 2}
    monkeypatch.setattr(bugs, "_gh", fake)
    return calls


def test_report_is_anonymous_and_defuses_mentions(monkeypatch):
    calls = _setup(monkeypatch)
    out = asyncio.run(bugs.file("fan@example.com", "bug", "Scores froze @everyone\nsecond line", "#/", "iPhone · Safari", "2026-10-13"))
    assert out["issue"] == 7 and out["title"] == "Scores froze @everyone"
    sent = calls[0][2]
    assert "fan@example.com" not in sent["body"] and "@​everyone" in sent["body"]
    assert sent["labels"] == ["from-app", "bug"] and bugs.sent_today("fan@example.com") == 1


def test_your_reports_pick_up_fixed_and_the_owner_reply(monkeypatch):
    _setup(monkeypatch)
    asyncio.run(bugs.file("fan@example.com", "idea", "Add a dark mode toggle please", "#/settings", "", ""))
    bugs._conn.execute("UPDATE bug_report SET checked='2000-01-01T00:00:00Z'")
    [r] = asyncio.run(bugs.mine("fan@example.com"))
    assert r["state"] == "fixed" and r["reply"] == "Fixed in today's update." and "email" not in r
    assert asyncio.run(bugs.mine("someone@else.com")) == []


def test_long_titles_are_cut():
    assert len(bugs.title_for("word " * 40)) <= 70
