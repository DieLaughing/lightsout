"""Weather at the track, only close to the event.

Places come from data/venues.yaml (matched on "<venue> <event name>"). Forecasts come from Open-Meteo (free, no key),
fetched only for places with a session in the next 7 days: every 6 hours, hourly once a session is less than 36
hours away. Each session gets the forecast for its start hour (or the day's high/low for sessions without a time).
Temperatures are kept in °C; the web app shows °F unless someone picks °C.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

from .adapters.base import get_json

log = logging.getLogger("lightsout.weather")

VENUES = Path(__file__).resolve().parent / "data" / "venues.yaml"
API = "https://api.open-meteo.com/v1/forecast"
AHEAD = timedelta(days=7)
SKIP_SERIES = {"w2rc"}  # rally-raids cross a country; one point's weather means nothing
CHECK_EVERY = 1800

_cache: dict[str, dict] = {}   # place name -> {"fetched": iso, "data": open-meteo response}
_data_dir: Path | None = None


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


@lru_cache(maxsize=1)
def places() -> list[dict]:
    out = []
    for p in yaml.safe_load(VENUES.read_text()):
        terms = list(dict.fromkeys(norm(t) for t in p["match"]))
        out.append({**p, "terms": terms, "rx": re.compile(r"\b(" + "|".join(map(re.escape, terms)) + r")\b")})
    return out


def place_for(venue: str | None, event_name: str | None) -> dict | None:
    text = norm(f"{venue or ''} {event_name or ''}")
    return next((p for p in places() if p["rx"].search(text)), None)


# ---- reading a forecast ---------------------------------------------------------------------------
def forecast_for(session: dict, data: dict, now: datetime | None = None) -> dict | None:
    """{c, rain, code} at the session's start hour, or {hi, lo, rain, code} for a day without a time."""
    now = now or datetime.now(timezone.utc)
    offset = timedelta(seconds=data.get("utc_offset_seconds") or 0)
    if session.get("start_utc"):
        start = datetime.fromisoformat(session["start_utc"].replace("Z", "+00:00"))
        if not (now - timedelta(hours=3) <= start <= now + AHEAD):
            return None
        hour = (start + offset).strftime("%Y-%m-%dT%H:00")
        h = data.get("hourly") or {}
        try:
            i = h["time"].index(hour)
        except (KeyError, ValueError):
            return None
        return {"c": h["temperature_2m"][i], "rain": h["precipitation_probability"][i], "code": h["weather_code"][i]}
    day = session.get("day")
    if not day or not (now.date() <= datetime.fromisoformat(day).date() <= (now + AHEAD).date()):
        return None
    d = data.get("daily") or {}
    try:
        i = d["time"].index(day)
    except (KeyError, ValueError):
        return None
    return {"hi": d["temperature_2m_max"][i], "lo": d["temperature_2m_min"][i],
            "rain": d["precipitation_probability_max"][i], "code": d["weather_code"][i]}


def attach(rows: list[dict]) -> list[dict]:
    """Adds `weather` to sessions (rows carry venue + event_name, as /api/sessions returns them)."""
    for r in rows:
        if r.get("series") in SKIP_SERIES:
            continue
        p = place_for(r.get("venue"), r.get("event_name"))
        entry = _cache.get(p["name"]) if p else None
        if entry:
            w = forecast_for(r, entry["data"])
            if w:
                (r.get("_s") or r)["weather"] = w  # _s: the original session when given a copy with event fields
    return rows


# ---- fetching ----------------------------------------------------------------------------------------
def _due(place: str, soonest: datetime, now: datetime) -> bool:
    entry = _cache.get(place)
    if not entry:
        return True
    age = now - datetime.fromisoformat(entry["fetched"])
    return age >= (timedelta(hours=1) if soonest - now < timedelta(hours=36) else timedelta(hours=6))


async def fetch(p: dict) -> dict:
    return await get_json(
        f"{API}?latitude={p['lat']}&longitude={p['lon']}&timezone=auto&forecast_days=8"
        "&hourly=temperature_2m,precipitation_probability,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code")


async def refresh(store) -> None:
    now = datetime.now(timezone.utc)
    rows = store.sessions(now.strftime("%Y-%m-%dT%H:%M:%SZ"), (now + AHEAD).strftime("%Y-%m-%dT%H:%M:%SZ"))
    soonest: dict[str, tuple[dict, datetime]] = {}
    unmatched = set()
    for r in rows:
        if r["series"] in SKIP_SERIES:
            continue
        p = place_for(r.get("venue"), r.get("event_name"))
        if not p:
            unmatched.add(f"{r['series']}: {r.get('venue') or '-'} / {r.get('event_name')}")
            continue
        when = datetime.fromisoformat((r.get("start_utc") or f"{r['day']}T12:00:00Z").replace("Z", "+00:00"))
        if p["name"] not in soonest or when < soonest[p["name"]][1]:
            soonest[p["name"]] = (p, when)
    for line in sorted(unmatched):
        log.info("no weather place for %s (add it to data/venues.yaml)", line)
    changed = False
    for name, (p, when) in soonest.items():
        if not _due(name, when, now):
            continue
        try:
            _cache[name] = {"fetched": now.isoformat(), "data": await fetch(p)}
            changed = True
        except Exception as exc:
            log.warning("weather for %s failed: %r", name, exc)
    if changed and _data_dir:
        (_data_dir / "weather.json").write_text(json.dumps(_cache))


async def loop(store, data_dir: Path) -> None:
    global _data_dir
    _data_dir = data_dir
    try:  # keep forecasts across restarts so a deploy doesn't refetch everything
        _cache.update(json.loads((data_dir / "weather.json").read_text()))
    except (OSError, ValueError):
        pass
    while True:
        try:
            await refresh(store)
        except Exception:
            log.exception("weather loop error")
        await asyncio.sleep(CHECK_EVERY)
