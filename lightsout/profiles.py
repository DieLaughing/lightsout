"""Driver pages' extras: a photo and two lines from Wikipedia (any series), and an F1 career from Jolpica.

Nothing is kept per driver until someone opens their page (or follows them): there are thousands of drivers across
the series, so profiles are built on demand, saved in the profile table and rebuilt after a day. A page never waits
on Wikipedia or Jolpica: get() answers from what's saved and starts a build in the background, and the page asks
again shortly. Photos are Wikimedia Commons thumbnails, downloaded once and served from the app's own address
(/img/d/...), so a phone never talks to Wikipedia and a page full of faces costs one small file each.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .adapters.base import _client, get_json
from .db import Store
from .follow import matches, normalise

log = logging.getLogger("lightsout.profiles")

WIKI = "https://en.wikipedia.org/w/api.php"
JOLPICA = "https://api.jolpi.ca/ergast/f1"
# Wikimedia asks API users to say who they are.
HEADERS = {"Api-User-Agent": "LightsOut/1.0 (https://github.com/DieLaughing/lightsout)"}
TTL = timedelta(days=1)
RETRY = 600            # seconds before a failed build is tried again
F1_SEASONS = 6         # drivers of the last few F1 seasons get a career (covers ones who moved series recently)
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
# A Wikipedia page is the driver's when its short description names a person who races (not a team, a season...).
PERSON = re.compile(r"\b(driver|racer|rider|motorcyclist|drifter)\b", re.I)
PRONOUNCE = re.compile(r"[A-Z][\w ]*?(?: pronunciation)?:\s*\[[^\]]*\];?\s*")

_building: dict[str, asyncio.Task] = {}
_failed: dict[str, float] = {}
_jolpica = asyncio.Lock()
_photo_dir: Path | None = None


def setup(data_dir: Path) -> None:
    global _photo_dir
    _photo_dir = data_dir / "img" / "d"


def photo_path(file: str) -> Path | None:
    return _photo_dir / file if _photo_dir and re.fullmatch(r"[0-9a-f]{16}\.(jpg|jpeg|png|webp)", file) else None


def _stale(fetched: str) -> bool:
    return datetime.now(timezone.utc) - datetime.fromisoformat(fetched.replace("Z", "+00:00")) > TTL


def get(store: Store, name: str) -> dict:
    """What's saved for `name` ({} if nothing yet) plus pending=True while a build is under way."""
    key = f"driver:{normalise(name)}"
    saved = store.cached(key)
    if saved is None or _stale(saved[1]) or saved[0].get("partial"):
        start(store, name)
    data = {k: v for k, v in (saved[0] if saved else {}).items() if k != "partial"}
    data["pending"] = key in _building
    return data


def start(store: Store, name: str) -> None:
    key = f"driver:{normalise(name)}"
    if key in _building or time.time() - _failed.get(key, 0) < RETRY:
        return
    task = asyncio.create_task(_build(store, name, key))
    _building[key] = task
    task.add_done_callback(lambda _: _building.pop(key, None))


async def _build(store: Store, name: str, key: str) -> None:
    old = (store.cached(key) or ({},))[0]
    try:
        f1_failed = None
        try:
            f1_id = await _f1_id(store, name)
        except Exception as exc:  # Jolpica busy: the photo and bio don't wait for it
            f1_id, f1_failed = None, exc
        wiki = await _wiki(store, name, await _f1_title(store, f1_id) if f1_id else None)
        if f1_id or f1_failed:  # shown while the career (up to ~25 Jolpica requests the first time) comes in
            store.save_cached(key, {"wiki": wiki, "f1": old.get("f1"), "partial": True})
        if f1_failed:
            raise f1_failed
        store.save_cached(key, {"wiki": wiki, "f1": await _f1_career(store, f1_id) if f1_id else None})
    except Exception as exc:  # a source being down: keep what's saved, try again later
        log.warning("profile %s failed: %r", name, exc)
        _failed[key] = time.time()


# ---- Wikipedia -------------------------------------------------------------------------------------
def _clean(extract: str) -> str:
    """Drop pronunciation guides: '(Catalan pronunciation: [ˈal…]; born 1 April 1997)' -> '(born 1 April 1997)'."""
    s = PRONOUNCE.sub("", (extract or "").split("\n")[0])
    s = re.sub(r"\(\s*;\s*", "(", s.replace("( ", "("))
    return re.sub(r"\s*\(\s*\)", "", s).strip()


def _core(title: str) -> str:
    return re.sub(r"\s*\([^)]*\)$", "", title)


def _display(name: str) -> str:
    """Grids sometimes shout surnames ('VERSTAPPEN Max'); Wikipedia titles are in normal case."""
    return " ".join(w.capitalize() if w.isupper() and len(w) > 2 else w for w in name.split())


async def _wiki(store: Store, name: str, title: str | None) -> dict | None:
    q = {"action": "query", "format": "json", "formatversion": "2", "redirects": "1",
         "prop": "pageimages|description|extracts|info", "inprop": "url", "exintro": "1", "explaintext": "1",
         "exsentences": "2", "piprop": "thumbnail|name", "pithumbsize": "180"}
    shown = _display(name)
    tries = [title] if title else [f"{shown} (racing driver)", f"{shown} (rally driver)", f"{shown} (motorcycle racer)",
                                   f"{shown} (racer)", shown]
    pages = (await _wiki_get({**q, "titles": "|".join(tries)})).get("pages") or []
    by_title = {p.get("title"): p for p in pages}
    # Redirects ('Alex Palou' -> 'Álex Palou') land under the target's title.
    order = [by_title.get(t) for t in tries] + pages
    page = next((p for p in order if p and _is_driver(p, name, title)), None)
    if not page and not title:
        found = (await _wiki_get({**q, "generator": "search", "gsrsearch": f"{shown} racing", "gsrlimit": "5"})).get("pages") or []
        page = next((p for p in sorted(found, key=lambda p: p.get("index", 99)) if _is_driver(p, name, None)), None)
    if not page:
        return None
    thumb = (page.get("thumbnail") or {}).get("source")
    photo = await _photo(thumb) if thumb else None
    return {"title": page["title"], "url": page.get("fullurl") or f"https://en.wikipedia.org/wiki/{quote(page['title'])}",
            "description": page.get("description") or "", "extract": _clean(page.get("extract") or ""),
            "photo": photo, "credit": await _credit(page["pageimage"]) if photo and page.get("pageimage") else None}


async def _credit(file: str) -> dict | None:
    """Who took the photo and its licence (Commons photos are CC BY / BY-SA: the page must say so)."""
    pages = (await _wiki_get({"action": "query", "format": "json", "formatversion": "2", "titles": f"File:{file}",
                              "prop": "imageinfo", "iiprop": "extmetadata|url"})).get("pages") or []
    info = ((pages[0].get("imageinfo") or [{}])[0]) if pages else {}
    meta = info.get("extmetadata") or {}
    artist = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value") or "").strip()
    return {"author": " ".join(artist.split())[:80], "license": (meta.get("LicenseShortName") or {}).get("value") or "",
            "url": info.get("descriptionurl") or ""}


def _is_driver(page: dict, name: str, title: str | None) -> bool:
    if page.get("missing") or not page.get("pageid"):
        return False
    if title:  # Jolpica's link is the driver's page
        return True
    return bool(PERSON.search(page.get("description") or "")) and matches(name, _core(page["title"]))


async def _wiki_get(params: dict) -> dict:
    return (await get_json(f"{WIKI}?" + "&".join(f"{k}={quote(v)}" for k, v in params.items()), HEADERS)).get("query") or {}


async def _photo(url: str) -> str | None:
    ext = Path(url.split("?")[0]).suffix.lower()
    if ext not in PHOTO_EXT or not _photo_dir:
        return None
    file = hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:16] + ext
    path = _photo_dir / file
    if not path.exists():
        r = await _client().get(url, headers=HEADERS)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(path)
    return f"/img/d/{file}"


# ---- F1 (Jolpica) ----------------------------------------------------------------------------------
async def _jolpica_get(store: Store, path: str, forever: bool = False) -> dict:
    """A Jolpica answer, saved: past seasons never change (forever), the rest are fetched again after TTL.
    Jolpica allows a few requests a second (and a few hundred an hour), so they go one at a time, two a second."""
    key = f"jolpica:{path}"
    saved = store.cached(key)
    if saved and (forever or not _stale(saved[1])):
        return saved[0]
    async with _jolpica:
        data = (await get_json(f"{JOLPICA}/{path}"))["MRData"]
        await asyncio.sleep(0.5)
    store.save_cached(key, data)
    return data


async def _f1_drivers(store: Store) -> list[dict]:
    year = datetime.now(timezone.utc).year
    out = {}
    for season in range(year, year - F1_SEASONS, -1):
        data = await _jolpica_get(store, f"{season}/drivers.json?limit=100", forever=season < year)
        for d in data["DriverTable"]["Drivers"]:
            out.setdefault(d["driverId"], d)
    return list(out.values())


async def _f1_id(store: Store, name: str) -> str | None:
    drivers = await _f1_drivers(store)
    full = {d["driverId"]: f"{d['givenName']} {d['familyName']}" for d in drivers}
    exact = next((i for i, n in full.items() if normalise(n) == normalise(name)), None)
    return exact or next((i for i, n in full.items() if matches(name, n)), None)


async def _f1_title(store: Store, driver_id: str) -> str | None:
    d = next((d for d in await _f1_drivers(store) if d["driverId"] == driver_id), None)
    url = (d or {}).get("url") or ""
    return url.rsplit("/wiki/", 1)[-1].replace("_", " ") if "/wiki/" in url else None


async def _f1_career(store: Store, driver_id: str) -> dict:
    races, offset, total = [], 0, 1
    while offset < total:
        data = await _jolpica_get(store, f"drivers/{driver_id}/results.json?limit=100&offset={offset}")
        total, offset = int(data["total"]), offset + 100
        races += data["RaceTable"]["Races"]
    sprints = (await _jolpica_get(store, f"drivers/{driver_id}/sprint.json?limit=100"))["RaceTable"]["Races"]
    year = datetime.now(timezone.utc).year
    seasons = []
    for s in sorted({int(r["season"]) for r in races}, reverse=True):
        lists = (await _jolpica_get(store, f"{s}/drivers/{driver_id}/driverstandings.json",
                                    forever=s < year))["StandingsTable"]["StandingsLists"]
        row = lists[0]["DriverStandings"][0] if lists and lists[0]["DriverStandings"] else None
        teams = list(dict.fromkeys(x["Results"][0]["Constructor"]["name"] for x in races if int(x["season"]) == s))
        seasons.append({"season": s, "teams": teams, "pos": row and row.get("positionText"),
                        "points": float(row["points"]) if row else 0, "wins": int(row["wins"]) if row else 0,
                        "races": sum(int(x["season"]) == s for x in races)})
    res = [r["Results"][0] for r in races]
    pos = [int(x["position"]) for x in res if x.get("positionText", "").isdigit()]
    first_win = next((r for r in races if r["Results"][0].get("positionText") == "1"), None)
    return {
        "races": len(races),
        "wins": sum(p == 1 for p in pos),
        "podiums": sum(p <= 3 for p in pos),
        "poles": sum(x.get("grid") == "1" for x in res),
        "fastest_laps": sum((x.get("FastestLap") or {}).get("rank") == "1" for x in res),
        "points": sum(float(x.get("points") or 0) for x in res)
                  + sum(float(r["SprintResults"][0].get("points") or 0) for r in sprints if r.get("SprintResults")),
        # Only finished seasons count: this season's leader isn't champion yet.
        "titles": [s["season"] for s in seasons if s["pos"] == "1" and s["season"] < year],
        "best": min(pos) if pos else None,
        "first": races and {"season": int(races[0]["season"]), "race": races[0]["raceName"]},
        "first_win": first_win and {"season": int(first_win["season"]), "race": first_win["raceName"]},
        "seasons": seasons,
    }


async def warm(store: Store, names: list[str]) -> None:
    """Build missing or old profiles for everyone people follow, one at a time (the refresh loop calls this)."""
    for name in names:
        saved = store.cached(f"driver:{normalise(name)}")
        if saved is None or _stale(saved[1]):
            start(store, name)
            task = _building.get(f"driver:{normalise(name)}")
            if task:
                await asyncio.wait([task])


WARM_EVERY = 6 * 3600


async def loop(store: Store) -> None:
    """Followed drivers' pages are ready before anyone opens them."""
    while True:
        try:
            names = {f["name"] for s in store.approved_settings().values() for f in s.get("follows") or [] if f.get("name")}
            await warm(store, sorted(names))
        except Exception:
            log.exception("profile warm-up error")
        await asyncio.sleep(WARM_EVERY)
