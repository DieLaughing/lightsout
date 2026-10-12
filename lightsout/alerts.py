"""Push alerts: session starts, cautions and red flags, things happening to people you follow, results, news.

Who gets what:
  * race-wide alerts (start, caution/SC/red flag, result) go to anyone following a driver or team in that series
  * personal alerts (incident, penalty, retirement, taking the lead, news) go to followers of that driver or team
Each alert has a key in the sent table, so nothing repeats; anything older than RECENT is never sent (a restart
or reconnect replays history into the event store, and that history isn't news).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import follow, watch
from .db import Store
from .push import Pusher
from .series import BY_ID

log = logging.getLogger("lightsout.alerts")

KINDS = ("start", "cautions", "incidents", "results", "news")
RECENT = timedelta(minutes=15)
NEWS_RECENT = timedelta(hours=6)
NEWS_PER_RUN = 4                 # per person, so a big news day doesn't bury the phone
START_WINDOW = (timedelta(minutes=-5), timedelta(minutes=10))
RACE_WIDE = {"caution": "cautions", "red_flag": "cautions", "result": "results"}
PERSONAL = {"incident", "investigation", "penalty", "retired", "lead"}


def prefs(settings: dict) -> dict:
    p = settings.get("notify") or {}
    return {k: bool(p.get(k, True)) for k in KINDS}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def matches(follows: set[str], names: list[str]) -> list[str]:
    """Which of `names` (drivers, teams) someone follows. Follows are free text, so "Red Bull" follows
    "Red Bull Racing" and the reverse."""
    hits = []
    for n in names:
        k = follow.normalise(n)
        if any(k == f or (len(f) >= 4 and (f in k or k in f)) for f in follows):
            hits.append(n)
    return hits


class Alerts:
    def __init__(self, store: Store, pusher: Pusher, base_url: str):
        self.store, self.pusher, self.base = store, pusher, base_url

    # ---- who wants what ---------------------------------------------------------------------
    def people(self) -> list[dict]:
        """Approved people with at least one device signed up, with their follows and the series those cover."""
        subs: dict[str, list[dict]] = {}
        for s in self.store.pushes():
            subs.setdefault(s["email"], []).append(s)
        out = []
        settings = self.store.approved_settings()
        for email, mine in subs.items():
            st = settings.get(email) or {}
            names = [f["name"] for f in st.get("follows") or [] if f.get("name")]
            series = {x for f in follow.status(self.store, names) for x in f["series"]} if names else set()
            out.append({"email": email, "subs": mine, "prefs": prefs(st), "series": series,
                        "follows": {follow.normalise(n) for n in names}})
        return out

    # ---- triggers -------------------------------------------------------------------------------
    async def on_events(self, events: list[dict]) -> None:
        cutoff = _iso(datetime.now(timezone.utc) - RECENT)
        events = [e for e in events if e["time"] >= cutoff and (e["kind"] in RACE_WIDE or e["kind"] in PERSONAL)]
        if not events:
            return
        fresh = [e for e in events if not self.store.was_sent(f"ev:{e['id']}")]
        self.store.mark_sent([f"ev:{e['id']}" for e in fresh])
        if not fresh:
            return
        for p in self.people():
            for e in fresh:
                s = BY_ID.get(e["series"])
                short = s.short if s else e["series"].upper()
                url = f"{self.base}/#/live/{e['series']}"
                if e["kind"] in RACE_WIDE:
                    if e["series"] not in p["series"] or not p["prefs"][RACE_WIDE[e["kind"]]]:
                        continue
                    body = e["text"]
                    if e["kind"] == "result":
                        mine = [f"{n} P{pos}" for n, pos in (e.get("positions") or {}).items() if matches(p["follows"], [n])]
                        if mine:
                            body += "\nYour drivers: " + ", ".join(mine)
                    title = {"caution": f"{short}: caution", "red_flag": f"{short}: red flag",
                             "result": f"{short}: result"}[e["kind"]]
                    if e["kind"] == "caution" and e["series"] in ("f1", "f2", "f3", "f1a"):
                        title = f"{short}: {e['text'].split(' deployed')[0]}"
                    await self.send(p["subs"], {"title": title, "body": body, "url": url, "tag": e["id"]})
                else:
                    who = matches(p["follows"], e.get("drivers", []) + e.get("teams", []))
                    if not who or not p["prefs"]["incidents"]:
                        continue
                    label = {"incident": "incident", "investigation": "under investigation", "penalty": "penalty",
                             "retired": "out", "lead": "leads"}[e["kind"]]
                    await self.send(p["subs"], {"title": f"{short} · {who[0]}: {label}", "body": e["text"],
                                                "url": url, "tag": e["id"]})

    async def check_starts(self) -> None:
        """Races and sprints starting now, for people following someone in them."""
        now = datetime.now(timezone.utc)
        rows = [r for r in self.store.sessions(_iso(now + START_WINDOW[0]), _iso(now + START_WINDOW[1]))
                if r["start_utc"] and r["kind"] in ("race", "sprint")
                and _iso(now + START_WINDOW[0]) <= r["start_utc"] <= _iso(now + START_WINDOW[1])]
        fresh = [r for r in rows if not self.store.was_sent(f"start:{r['series']}:{r['event_key']}:{r['key']}")]
        if not fresh:
            return
        self.store.mark_sent([f"start:{r['series']}:{r['event_key']}:{r['key']}" for r in fresh])
        for p in self.people():
            for r in fresh:
                if r["series"] not in p["series"] or not p["prefs"]["start"]:
                    continue
                w = watch.watch_for(r["series"], r["kind"], r.get("tv"))
                where = f"Watch: {w['label']}" if w else ""
                await self.send(p["subs"], {"title": f"{BY_ID[r['series']].short}: {r['event_name']} {r['name']} is starting",
                                            "body": where, "url": f"{self.base}/#/live/{r['series']}",
                                            "tag": f"start:{r['series']}:{r['key']}"})

    async def on_news(self, items: list[dict]) -> None:
        cutoff = _iso(datetime.now(timezone.utc) - NEWS_RECENT)
        items = [n for n in items if n["time"] >= cutoff and not self.store.was_sent(f"news:{n['id']}")]
        self.store.mark_sent([f"news:{n['id']}" for n in items])
        if not items:
            return
        for p in self.people():
            if not p["prefs"]["news"]:
                continue
            sent = 0
            for n in sorted(items, key=lambda n: n["time"], reverse=True):
                who = matches(p["follows"], n["drivers"] + n["teams"])
                if not who or sent >= NEWS_PER_RUN:
                    continue
                sent += 1
                await self.send(p["subs"], {"title": f"{who[0]} · {n['source']}", "body": n["title"],
                                            "url": n["url"], "tag": n["id"]})

    async def send(self, subs: list[dict], payload: dict) -> int:
        delivered = 0
        for s in subs:
            result = await self.pusher.send(s["sub"], payload)
            if result == "gone":
                self.store.remove_push(s["endpoint"])
            delivered += result == "ok"
        return delivered
