"""Where a race is, in words people use: "Braselton, GA", "Phillip Island, VIC", "Spielberg, Austria".

Tracks come from data/venues.yaml (the same list the weather uses): town, region (US states, Canadian provinces and
Australian states, where people expect one) and country code. Track pages (#/track/<slug>) gather every series racing
at a place; "Near me" measures from a home point the person sets once (a US ZIP code via zippopotam.us, or their
device's location rounded to about 10 km).
"""
from __future__ import annotations

import math
import re

from . import weather
from .adapters.base import get_json, slugify

COUNTRIES = {
    "us": "USA", "ca": "Canada", "mx": "Mexico", "br": "Brazil", "ar": "Argentina", "cl": "Chile", "py": "Paraguay",
    "gb": "UK", "nl": "Netherlands", "de": "Germany", "cz": "Czechia", "hu": "Hungary", "it": "Italy", "es": "Spain",
    "be": "Belgium", "fr": "France", "mc": "Monaco", "pt": "Portugal", "at": "Austria", "az": "Azerbaijan",
    "se": "Sweden", "hr": "Croatia", "gr": "Greece", "ee": "Estonia", "fi": "Finland", "lv": "Latvia", "pl": "Poland",
    "qa": "Qatar", "ae": "UAE", "bh": "Bahrain", "sa": "Saudi Arabia", "ke": "Kenya", "za": "South Africa",
    "my": "Malaysia", "id": "Indonesia", "th": "Thailand", "cn": "China", "jp": "Japan", "au": "Australia",
    "mo": "Macau", "sg": "Singapore",
}


def label(p: dict) -> str:
    town, region = p.get("town") or "", p.get("region") or ""
    country = COUNTRIES.get(str(p.get("cc") or ""), "")
    if region:
        return f"{town}, {region}"
    if not town or town == country:
        return country or town
    return f"{town}, {country}" if country else town


def info(p: dict) -> dict:
    return {"slug": slugify(p["name"]), "name": p["name"], "label": label(p), "lat": p["lat"], "lon": p["lon"]}


def by_slug(slug: str) -> dict | None:
    return next((p for p in weather.places() if slugify(p["name"]) == slug), None)


def for_event(venue: str | None, name: str | None) -> dict | None:
    p = weather.place_for(venue, name)
    return info(p) if p else None


def tag(rows: list[dict]) -> list[dict]:
    """Adds `place` to sessions or events that carry venue + event_name (or name)."""
    for r in rows:
        place = for_event(r.get("venue"), r.get("event_name") or r.get("name"))
        if place:
            r["place"] = place
    return rows


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


async def zip_home(code: str) -> dict | None:
    """A US ZIP code -> {label: "Ocala, FL", lat, lon}. None when the ZIP is unknown."""
    code = re.sub(r"\D", "", code or "")[:5]
    if len(code) != 5:
        return None
    try:
        data = await get_json(f"https://api.zippopotam.us/us/{code}")
    except Exception:
        return None
    pl = (data.get("places") or [None])[0]
    if not pl:
        return None
    return {"label": f"{pl['place name']}, {pl['state abbreviation']}", "lat": round(float(pl["latitude"]), 2),
            "lon": round(float(pl["longitude"]), 2)}
