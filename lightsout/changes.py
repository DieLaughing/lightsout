"""What's new: the app's update history, from data/changes.yaml (newest first).

The same file feeds the What's new page (/api/changes), the "new" dot and banner, CHANGELOG.md on GitHub
(tools/changelog.py) and one GitHub Release per day (tools/release.py).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent / "data" / "changes.yaml"
KIND_LABEL = {"new": "New", "improved": "Improved", "fixed": "Fixed"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load(path: Path = DATA) -> list[dict]:
    out = []
    for e in yaml.safe_load(path.read_text()) or []:
        d = e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"]))
        out.append({"id": f"{d.isoformat()}-{_slug(e['title'])}", "date": d.isoformat(), "kind": e.get("kind", "new"),
                    "title": e["title"], "text": e["text"], "link": e.get("link")})
    return out


def by_date(entries: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for e in entries:
        days.setdefault(e["date"], []).append(e)
    return days


def nice_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d:%B} {d.day}, {d.year}"


def day_notes(entries: list[dict]) -> str:
    return "\n".join(f"- **{KIND_LABEL.get(e['kind'], e['kind'].title())}: {e['title']}.** {e['text']}" for e in entries)


def markdown(entries: list[dict], app: str, site: str) -> str:
    lines = [f"# What's new in {app}", "",
             f"Every update to [{app}]({site}), newest first. The same list is on the app's What's new page.",
             "This file is generated from `data/changes.yaml` by `tools/changelog.py`; edit that file, not this one.", ""]
    for day, items in by_date(entries).items():
        lines += [f"## {nice_date(day)}", "", day_notes(items), ""]
    return "\n".join(lines)
