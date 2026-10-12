"""Shared helpers for adapters: fetching and schema.org parsing."""
from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import date, datetime
from html.parser import HTMLParser

from curl_cffi.requests import AsyncSession

# Several sites (wrc.com, fiawec.com) block non-browser TLS fingerprints, so every
# request impersonates Chrome.
_session: AsyncSession | None = None
_session_loop = None


def _client() -> AsyncSession:
    # One shared client per event loop (scripts and tests may start several loops).
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is None or _session_loop is not loop:
        _session, _session_loop = AsyncSession(impersonate="chrome", timeout=30), loop
    return _session


async def get_text(url: str, headers: dict | None = None) -> str:
    r = await _client().get(url, headers=headers)
    r.raise_for_status()
    return r.text


async def get_json(url: str, headers: dict | None = None):
    r = await _client().get(url, headers=headers)
    r.raise_for_status()
    return r.json()


def json_ld(page: str) -> list:
    """All schema.org JSON-LD blocks on a page."""
    out = []
    for raw in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            out.append(json.loads(html.unescape(raw)))
        except ValueError:
            continue
    return out


def find_sports_event(blocks: list) -> dict | None:
    """The first top-level SportsEvent, the one carrying subEvents if any."""
    for b in blocks:
        for item in b if isinstance(b, list) else [b]:
            if isinstance(item, dict) and item.get("@type") == "SportsEvent":
                return item
    return None


def raw_json_after(page: str, marker: str):
    """Decode the JSON value that follows `marker` in a page (Next.js RSC payloads)."""
    text = page.replace('\\"', '"')
    i = text.find(marker)
    if i < 0:
        return None
    value, _ = json.JSONDecoder().raw_decode(text[i + len(marker):])
    return value


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_local_midnight(dt: datetime) -> bool:
    """Sites publish placeholder sessions at 00:00 local before the timetable is out."""
    return dt.hour == 0 and dt.minute == 0


def tidy_caps(name: str) -> str:
    """"TOYOTA GAZOO RACING WRT" -> "Toyota Gazoo Racing WRT": de-shout names, keeping short acronyms."""
    if not name or name.strip() in ("None", "null"):
        return ""
    if not name.isupper():
        return name
    return " ".join(w if len(w) <= 3 or any(c.isdigit() for c in w) else w.capitalize() for w in name.split())


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def day(value: str) -> date:
    return date.fromisoformat(value[:10])


class _TableParser(HTMLParser):
    """Collects every <table> as a list of rows of cell text (th and td alike)."""

    # Also understands ARIA tables built from divs (role="table" / "row" / "cell"), as on wrc.com.
    # For those, the element stack tells us which div closes the row or cell.
    _ROLE = {"table": "table", "row": "tr", "cell": "td", "columnheader": "th", "rowheader": "th"}

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._stack: list[str] = []   # tag or the role it plays, per open element

    def handle_starttag(self, tag, attrs):
        role = self._ROLE.get(dict(attrs).get("role") or "", tag)
        if tag not in ("br", "img", "input", "meta", "link", "hr", "source", "wbr"):
            self._stack.append(role)
        if role == "table":
            self._depth += 1
            self.tables.append([])
        elif role == "tr" and self._depth:
            self._row = []
        elif role in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("br", "img", "input", "meta", "link", "hr", "source", "wbr") or not self._stack:
            return
        role = self._stack.pop()
        if role in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif role == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None
        elif role == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_tables(page: str) -> list[list[list[str]]]:
    p = _TableParser()
    p.feed(page)
    return [t for t in p.tables if t]
