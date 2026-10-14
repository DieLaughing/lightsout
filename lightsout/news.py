"""Racing news for the Wire: free RSS feeds, tagged with series, drivers and teams.

Motorsport.com has one feed per series, so its stories arrive knowing their series; the mixed feeds
(RACER, The Race) get series from whoever the story mentions. We link out and never copy articles.
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape

from .adapters.base import get_text
from .names import Tagger

log = logging.getLogger("lightsout.news")

MS = "https://www.motorsport.com/rss/{}/news/"
NASCAR = ["cup", "noaps", "truck"]
# (feed url, source label, series the feed is about or None for mixed, series to search for names)
FEEDS = [
    (MS.format("f1"), "Motorsport.com", ["f1"], ["f1"]),
    (MS.format("f2"), "Motorsport.com", ["f2"], ["f2"]),
    (MS.format("fia-f3"), "Motorsport.com", ["f3"], ["f3"]),
    (MS.format("f1-academy"), "Motorsport.com", ["f1a"], ["f1a"]),
    (MS.format("formula-e"), "Motorsport.com", ["fe"], ["fe"]),
    (MS.format("indycar"), "Motorsport.com", ["indycar"], ["indycar"]),
    (MS.format("wec"), "Motorsport.com", ["wec"], ["wec"]),
    (MS.format("nascar-cup"), "Motorsport.com", ["cup"], NASCAR),
    (MS.format("nascar-truck"), "Motorsport.com", ["truck"], NASCAR),
    (MS.format("wrc"), "Motorsport.com", ["wrc"], ["wrc"]),
    (MS.format("motogp"), "Motorsport.com", ["motogp"], ["motogp"]),
    (MS.format("moto2"), "Motorsport.com", ["moto2"], ["moto2"]),
    (MS.format("moto3"), "Motorsport.com", ["moto3"], ["moto3"]),
    (MS.format("dakar"), "Motorsport.com", ["w2rc"], ["w2rc"]),
    # All GT racing (GT4 America, IMSA GTD...): stories join a GT series only when they name someone on its grid.
    (MS.format("gt"), "Motorsport.com", None, None),
    # The series' own posts: no NLS grid to tag from, so names are looked up across every series (Verstappen in F1).
    ("https://www.nuerburgring-langstrecken-serie.de/language/en/feed/", "NLS", ["nls"], None),
    ("https://www.autosport.com/rss/feed/f1", "Autosport", ["f1"], ["f1"]),
    ("https://racer.com/feed/", "RACER", None, None),
    ("https://www.the-race.com/feed/", "The Race", None, None),
    ("https://dirtfish.com/feed/", "DirtFish", ["wrc"], ["wrc", "ara"]),
]


def _clean(s: str) -> str:
    s = unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.split(r"\bThe post\b|\bContinue reading\b|\bRead more\b", s)[0]
    return re.sub(r"\s+", " ", s).strip()


def parse(xml: str, source: str, series: list[str] | None, scope: list[str] | None, tagger: Tagger) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        text = _clean(item.findtext("description") or "")[:320]
        try:
            when = parsedate_to_datetime(item.findtext("pubDate")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue
        drivers, teams = tagger.tag(f"{title} {text}", scope)
        tagged = sorted({s for d in drivers for s in tagger.people[d]} & set(scope or tagger.people_series()))
        found = sorted(set(series or []) | set(tagged))
        if not found:
            continue  # a mixed feed's story about a series we don't cover (IMSA, MotoGP...)
        out.append({"id": "news:" + hashlib.sha1(link.encode()).hexdigest()[:16], "time": when, "title": title,
                    "text": text, "url": link, "source": source, "series": found, "drivers": drivers, "teams": teams})
    return out


async def fetch_all(tagger: Tagger) -> tuple[list[dict], dict[str, str | None]]:
    """Every feed; returns (items, {source url: error or None})."""
    items, status = [], {}
    for url, source, series, scope in FEEDS:
        try:
            items += parse(await get_text(url), source, series, scope, tagger)
            status[url] = None
        except Exception as exc:  # one broken feed never stops the rest
            log.warning("news feed %s failed: %r", url, exc)
            status[url] = f"{type(exc).__name__}: {exc}"[:200]
    return items, status
