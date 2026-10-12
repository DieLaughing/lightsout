"""Race-specific ticket pages where a series has them. Today that's F1: tickets.formula1.com lists a page per
Grand Prix on sale (f1-3320-united-states). F2, F3 and F1 Academy race on F1 weekends, so they use the page of
the Grand Prix on the same dates. Everything else falls back to data/tickets.yaml (watch.tickets_for).
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata

from . import watch
from .adapters.base import get_text

log = logging.getLogger("lightsout.tickets")

F1_STORE = "https://tickets.formula1.com/en"
F1_FAMILY = {"f1", "f2", "f3", "f1a"}
REFRESH = 12 * 3600
# Store slugs name the country; our events name the race. A few need a hint.
ALIASES = {"brazil": ["sao paulo", "brazil", "brazilian", "interlagos"], "united states": ["united states", "austin", "americas"],
           "mexico": ["mexico"], "abu dhabi": ["abu dhabi", "yas"], "las vegas": ["las vegas"],
           "great britain": ["british", "silverstone"], "netherlands": ["dutch", "zandvoort"]}

f1_pages: list[dict] = watch.f1_race_pages()  # from tickets.yaml; replaced by a fresh read of the store


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def parse_store(page: str) -> list[dict]:
    out = {}
    for path, slug in re.findall(r'href="(/en/f1-\d+-([a-z0-9-]+))"', page):
        out[slug] = {"slug": slug.replace("-", " "), "url": "https://tickets.formula1.com" + path}
    return list(out.values())


async def refresh_loop() -> None:
    """Re-read the store twice a day. It sometimes sends visitors to a waiting-room queue (queue-fair): that page
    has no race links, so the list from tickets.yaml simply stays in place."""
    global f1_pages
    while True:
        try:
            pages = parse_store(await get_text(F1_STORE))
            if pages:
                known = {p["slug"] for p in pages}
                f1_pages = pages + [p for p in watch.f1_race_pages() if p["slug"] not in known]
        except Exception as exc:
            log.warning("F1 ticket store fetch failed: %r", exc)
        await asyncio.sleep(REFRESH)


def f1_page_for(event: dict, pages: list[dict] | None = None) -> str | None:
    """The store page for an F1 event, matched on its race name first, then its venue."""
    pages = f1_pages if pages is None else pages
    # Not the country: Miami, Austin and Las Vegas are all in the United States.
    name, rest = _norm(event.get("name") or event.get("event_name") or ""), _norm(event.get("venue") or "")
    for field in (name, rest):
        for p in pages:
            words = ALIASES.get(p["slug"], [p["slug"]])
            if any(re.search(rf"\b{re.escape(w)}\b", field) for w in words):
                return p["url"]
    return None


def for_event(series: str, event: dict, f1_events: list[dict]) -> dict | None:
    """{url, label} for one event. `event` has name/venue/country and start/end days; `f1_events` is the F1
    calendar, for the support series."""
    base = watch.tickets_for(series, event.get("url"))
    if series not in F1_FAMILY:
        return base
    if series == "f1":
        gp = event
    else:  # the Grand Prix whose dates overlap this support-series round
        start, end = event.get("start_day") or event.get("event_start"), event.get("end_day") or event.get("event_end")
        gp = next((e for e in f1_events if start and end and e.get("start_day") and e["start_day"] <= end
                   and (e.get("end_day") or e["start_day"]) >= start), None)
    url = f1_page_for(gp) if gp else None
    if not url:
        return base
    return {"url": url, "label": f"F1 tickets: {gp.get('name') or gp.get('event_name')}"}
