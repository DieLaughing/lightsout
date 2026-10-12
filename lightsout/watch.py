"""US where-to-watch lookup from data/broadcasts.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent / "data" / "broadcasts.yaml"


@lru_cache(maxsize=1)
def _rules() -> dict:
    return yaml.safe_load(DATA.read_text())


def watch_for(series: str, kind: str, tv: str | None = None) -> dict | None:
    """Where to watch a session. When the source names the session's network (NASCAR does, per race)
    and broadcasts.yaml knows that network, that wins over the series-wide rules."""
    if tv:
        net = next((v for k, v in (_rules().get("tv") or {}).items() if k.lower() == tv.strip().lower()), None)
        if net:
            return {"badge": net.get("badge"), "label": net.get("label", tv), "url": net.get("url"), "note": net.get("note")}
    for rule in _rules().get(series) or []:
        if not rule.get("kinds") or kind in rule["kinds"]:
            return {k: rule.get(k) for k in ("badge", "label", "url", "note")}
    return None


def season() -> int:
    return _rules().get("season")


TICKETS = Path(__file__).resolve().parent / "data" / "tickets.yaml"


@lru_cache(maxsize=1)
def _tickets() -> dict:
    return yaml.safe_load(TICKETS.read_text()) or {}


def tickets_for(series: str, event_url: str | None = None) -> dict | None:
    """{url, label} for buying tickets to an event of this series, or None."""
    t = _tickets().get(series)
    if not isinstance(t, dict) or not t.get("label"):
        return None
    url = event_url if t.get("event_page") else t.get("url")
    return {"url": url, "label": t.get("label")} if url else None


def f1_race_pages() -> list[dict]:
    return [{"slug": k.replace("-", " "), "url": v} for k, v in (_tickets().get("f1_races") or {}).items()]
