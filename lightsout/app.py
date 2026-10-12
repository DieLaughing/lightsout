"""Lights Out web server: JSON API, calendar feeds and the web app."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import access, adapters, alerts, changes, follow, ics, news, refresh, tickets, watch
from .db import Store
from .live.hub import LiveHub
from .push import Pusher
from .models import EVENT, PRACTICE, QUALIFYING, RACE, RALLY_DAY, SPRINT
from .series import BY_ID, SERIES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

DATA_DIR = Path(os.environ.get("LIGHTSOUT_DATA", Path(__file__).resolve().parent.parent / "data"))
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_KINDS = {PRACTICE, QUALIFYING, SPRINT, RACE, RALLY_DAY, EVENT}


def _cal_token() -> str:
    """Secret in calendar URLs. Those URLs skip Cloudflare Access, so this is their only guard."""
    if os.environ.get("LIGHTSOUT_CAL_TOKEN"):
        return os.environ["LIGHTSOUT_CAL_TOKEN"]
    path = DATA_DIR / "cal_token"
    if not path.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(24))
    return path.read_text().strip()


store = Store(DATA_DIR / "lightsout.db")
CAL_TOKEN = _cal_token()
live = LiveHub(store)
pusher = Pusher(DATA_DIR, access.ADMIN)
alerter = alerts.Alerts(store, pusher, access.BASE_URL)
live.alerts = alerter
log = logging.getLogger("lightsout.app")
NEWS_EVERY = 600


async def news_loop() -> None:
    """Fetch every news feed every 10 minutes. The first fetch after startup only fills the store:
    stories that were already out aren't news to anyone's phone."""
    first = True
    while True:
        try:
            items, status = await news.fetch_all(live.tagger())
            fresh = store.add_news(items)
            failing = [u for u, err in status.items() if err]
            store.record_run("news", not failing or len(failing) < len(status),
                             error="; ".join(f"{u}: {status[u]}" for u in failing)[:500] or None, counts=(len(fresh), len(items)))
            if first:
                store.mark_sent([f"news:{n['id']}" for n in items])
                first = False
            else:
                await alerter.on_news(fresh)
        except Exception:
            log.exception("news loop error")
        await asyncio.sleep(NEWS_EVERY)


async def starts_loop() -> None:
    while True:
        try:
            await alerter.check_starts()
        except Exception:
            log.exception("start alerts error")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(live.loop()), asyncio.create_task(live.events_loop()),
             asyncio.create_task(starts_loop())]
    if not os.environ.get("LIGHTSOUT_NO_REFRESH"):
        tasks += [asyncio.create_task(refresh.loop(store)), asyncio.create_task(news_loop()),
                  asyncio.create_task(tickets.refresh_loop())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Lights Out", lifespan=lifespan, docs_url=None, redoc_url=None)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f1_calendar() -> list[dict]:
    return [{k: e.get(k) for k in ("name", "venue", "country", "start_day", "end_day")} for e in store.events("f1")]


def _with_watch(rows: list[dict]) -> list[dict]:
    f1_events = _f1_calendar() if any(r["series"] in tickets.F1_FAMILY for r in rows) else []
    for r in rows:
        r["watch"] = watch.watch_for(r["series"], r["kind"], r.get("tv"))
        if r["kind"] in ("race", "sprint", "rally_day", "event"):  # tickets matter for race days, not every practice
            r["tickets"] = tickets.for_event(r["series"], r, f1_events)
    return rows


@app.get("/api/series")
def list_series():
    runs = store.runs()
    return [{**s.__dict__, "status": runs.get(s.id),
             "has_standings": hasattr(adapters.load(s.adapter), "standings")} for s in SERIES]


@app.get("/api/sessions")
def list_sessions(start: str | None = None, end: str | None = None, series: str | None = None):
    now = datetime.now(timezone.utc)
    start = start or _iso(now - timedelta(hours=12))
    end = end or _iso(now + timedelta(days=60))
    ids = [s for s in series.split(",") if s in BY_ID] if series else None
    return _with_watch(store.sessions(start, end, ids))


@app.get("/api/series/{series_id}/events")
def series_events(series_id: str):
    if series_id not in BY_ID:
        raise HTTPException(404)
    events = store.events(series_id)
    f1_events = _f1_calendar() if series_id in tickets.F1_FAMILY else []
    for e in events:
        e["tickets"] = tickets.for_event(series_id, e, f1_events)
        for s in e["sessions"]:
            s["watch"] = watch.watch_for(series_id, s["kind"], s.get("tv"))
    return events


@app.get("/api/series/{series_id}/standings")
def series_standings(series_id: str):
    if series_id not in BY_ID:
        raise HTTPException(404)
    return store.standings(series_id) or {}


@app.get("/api/live")
def live_summary():
    return live.summary()


@app.get("/api/live/{series_id}")
def live_series(series_id: str):
    return live.snapshot(series_id) or {}


@app.get("/api/entries")
def list_entries():
    return store.entries()


@app.post("/api/follow-status")
async def follow_status(request: Request):
    """Where each followed name appears across all grids and entry lists. Follows live in the browser,
    so the names arrive with the request."""
    body = await request.json()
    names = [str(n)[:80] for n in (body.get("names") or [])][:50]
    return follow.status(store, names)



@app.get("/api/config")
def config():
    return {"cal_token": CAL_TOKEN, "watch_season": watch.season()}


@app.get("/api/changes")
def whats_new():
    return changes.load()


@app.get("/healthz")
def healthz():
    return {"ok": True}  # for Docker's health check; reveals nothing


@app.get("/api/health")
def health():
    return {"ok": True, "runs": store.runs()}


@app.get("/cal/{token}/{codes}.ics")
def calendar(token: str, codes: str, request: Request, kinds: str | None = None):
    if not secrets.compare_digest(token, CAL_TOKEN):
        raise HTTPException(404)
    ids = [c for c in codes.split("+") if c in BY_ID]
    if not ids:
        raise HTTPException(404)
    wanted = set(kinds.split(",")) & DEFAULT_KINDS if kinds else DEFAULT_KINDS
    now = datetime.now(timezone.utc)
    rows = [r for r in store.sessions(_iso(now - timedelta(days=30)), _iso(now + timedelta(days=400)), ids)
            if r["kind"] in wanted]
    base = os.environ.get("LIGHTSOUT_BASE_URL") or str(request.base_url).rstrip("/")
    return Response(ics.build(rows, base), media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "max-age=900"})


def _asset_version() -> str:
    """Changes whenever any web file changes, so browsers and Cloudflare fetch fresh copies."""
    return hashlib.sha1("".join(f"{p.name}{p.stat().st_mtime_ns}" for p in sorted(WEB_DIR.rglob("*"))
                                if p.is_file()).encode()).hexdigest()[:10]


# Paths anyone signed in may reach before approval. /act and /cal are reached without Cloudflare at all
# (ntfy buttons, calendar apps) and guard themselves with signed links / the calendar token.
OPEN_API = {"/api/me", "/api/request"}
REREQUEST_AFTER = timedelta(days=7)    # a denied or revoked person may ask again after this
RENOTIFY_AFTER = timedelta(hours=12)   # pressing Request twice doesn't ping the phone twice


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    email = access.email_from(request.headers)
    request.state.email = email
    user = store.user(email) if email else None
    if email and access.is_admin(email) and (not user or user["status"] != "approved"):
        store.set_status(email, "approved")  # the owner never waits on themself
        user = store.user(email)
    request.state.user = user
    if path.startswith("/api/") and path not in OPEN_API:
        if not email:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        if not user or user["status"] != "approved":
            return JSONResponse({"error": "not approved"}, status_code=403)
        if path.startswith("/api/admin") and not access.is_admin(email):
            return JSONResponse({"error": "admin only"}, status_code=403)
    response = await call_next(request)
    if not path.startswith(("/api/", "/cal/")):
        # Without this, Cloudflare caches .js/.css for hours and new versions don't show up.
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@app.get("/api/me")
def me(request: Request):
    email, user = request.state.email, request.state.user
    return {"email": email, "status": (user or {}).get("status", "none") if email else "signed-out",
            "admin": access.is_admin(email),
            "settings": store.settings(email) if user and user["status"] == "approved" else None}


@app.post("/api/request")
async def request_access(request: Request):
    email, user = request.state.email, request.state.user
    if not email:
        raise HTTPException(401)
    now = datetime.now(timezone.utc)
    status = (user or {}).get("status")
    if status == "approved":
        return {"status": "approved"}
    if status in ("denied", "revoked") and user.get("decided") and now - _parse(user["decided"]) < REREQUEST_AFTER:
        return {"status": status}
    if status == "pending" and user.get("requested") and now - _parse(user["requested"]) < RENOTIFY_AFTER:
        return {"status": "pending"}
    nonce = secrets.token_urlsafe(9)
    store.set_status(email, "pending", nonce)
    await access.notify_request(DATA_DIR, email, nonce)
    return {"status": "pending"}


@app.post("/act/{token}")
async def act(token: str):
    """Approve/Deny buttons in the ntfy push. Single-use: the nonce dies with the first decision.
    ntfy shows nothing when a button works, so a short confirmation push follows."""
    link = access.verify(DATA_DIR, token)
    if not link or link["action"] not in ("approve", "deny"):
        return PlainTextResponse("This link is invalid or has expired.", status_code=410)
    email, status = link["email"], "approved" if link["action"] == "approve" else "denied"
    user = store.user(email)
    if user and user["status"] == status and not user["nonce"]:
        return PlainTextResponse(f"{email} was already {status}.")  # a second tap on the same button
    if not user or user["status"] != "pending" or user["nonce"] != link["nonce"]:
        return PlainTextResponse("Already handled.", status_code=409)
    store.set_status(email, status)
    await access.notify(f"{email} {status}",
                        "They're in. Their next page load shows the app." if status == "approved"
                        else "They'll see a no-access page.", tags="white_check_mark" if status == "approved" else "x")
    return PlainTextResponse(f"{email}: {status}.")


@app.get("/invite/{code}")
async def invite(code: str, request: Request):
    email = request.state.email
    if not email:
        raise HTTPException(401)
    user = request.state.user
    if user and user["status"] == "approved":
        return RedirectResponse("/")
    if not store.use_invite(code, email):
        return HTMLResponse("<p>This invite link has already been used or doesn't exist. "
                            "<a href='/'>Request access instead</a>.</p>", status_code=410)
    store.set_status(email, "approved")
    await access.notify("Lights Out: invite used", f"{email} joined with an invite link.", tags="tada")
    return RedirectResponse("/")


@app.get("/api/admin")
def admin_overview():
    return {"users": [{k: u[k] for k in ("email", "status", "requested", "decided")} for u in store.users()],
            "invites": [{**i, "link": f"{access.BASE_URL}/invite/{i['code']}"} for i in store.invites()]}


@app.post("/api/admin/users")
async def admin_set_user(request: Request):
    body = await request.json()
    email, status = (body.get("email") or "").strip().lower(), body.get("status")
    if not email or status not in ("approved", "denied", "revoked"):
        raise HTTPException(400)
    if access.is_admin(email):
        raise HTTPException(400, "can't change the owner")
    store.set_status(email, status)
    return {"ok": True}


@app.post("/api/admin/invites")
async def admin_new_invite(request: Request):
    body = await request.json()
    code = secrets.token_urlsafe(12)
    store.add_invite(code, (body.get("note") or "")[:80] or None)
    return {"code": code, "link": f"{access.BASE_URL}/invite/{code}"}


@app.delete("/api/admin/invites/{code}")
def admin_delete_invite(code: str):
    store.delete_invite(code)
    return {"ok": True}


# ---- settings (follows, series, calendar kinds, alerts) now live on each person's account ----
@app.put("/api/settings")
async def save_settings(request: Request):
    body = await request.json()
    clean = {}  # only what was sent: a missing key means "never chosen", so the app's default applies
    if "series" in body:
        clean["series"] = [x for x in body["series"] or [] if x in BY_ID]
    if "follows" in body:
        clean["follows"] = [{"name": str(f.get("name"))[:80]} for f in (body["follows"] or [])[:100]
                            if isinstance(f, dict) and f.get("name")]
    if "seenChanges" in body:
        clean["seenChanges"] = str(body["seenChanges"] or "")[:120]  # newest What's new entry this person has seen
    if "seenSeries" in body:
        clean["seenSeries"] = [x for x in body["seenSeries"] or [] if x in BY_ID]
    if "kinds" in body:
        clean["kinds"] = [k for k in body["kinds"] or [] if k in DEFAULT_KINDS]
    if "dismissed" in body:
        clean["dismissed"] = [str(x)[:200] for x in (body["dismissed"] or [])][-500:]
    if "notify" in body:
        clean["notify"] = {k: bool((body["notify"] or {}).get(k, True)) for k in alerts.KINDS}
    store.save_settings(request.state.email, clean)
    return clean


@app.get("/api/person/{name}")
def person(name: str):
    """Everything known about one followed name (driver or team): where they race, what's next, standings,
    their last result. News and race events come from /api/wire?driver=."""
    name = name.strip()[:80]
    st = follow.status(store, [name])[0]
    now = datetime.now(timezone.utc)
    rides = [{"series": e["series"], "name": e["name"], "team": e["team"], "number": e["number"]}
             for e in store.entries() if follow.matches(name, e["name"]) or (e["team"] and follow.matches(name, e["team"]))]
    # Upcoming sessions: every session of a series they're on the grid of (not pick-your-events series),
    # plus every session of an event whose entry list has them.
    grid = {f["series"] for f in st["found"] if f["kind"] == "grid" and f["series"] not in follow.PICK_EVENTS}
    entered = {(f["series"], f["event_key"]) for f in st["found"] if f["kind"] == "entry"}
    upcoming = [r for r in store.sessions(_iso(now), _iso(now + timedelta(days=120)))
                if (r["series"] in grid or (r["series"], r["event_key"]) in entered)
                and (r["start_utc"] or r["day"] + "T23:59:59Z") >= _iso(now)]
    # The sessions that decide things (qualifying, sprints, races, rally days), not every practice.
    upcoming = [r for r in upcoming if r["kind"] in ("qualifying", "sprint", "race", "rally_day", "event")]
    _with_watch(upcoming)
    standings, results = [], []
    for sid in st["series"]:
        table = store.standings(sid) or {}
        rows = list(table.get("drivers") or []) + [r for rows_ in (table.get("classes") or {}).values() for r in rows_]
        row = next((r for r in rows if follow.matches(name, r["name"])), None)
        if row:
            lead = (table.get("drivers") or [{}])[0].get("points", 0)
            standings.append({"series": sid, "pos": row["pos"], "points": row["points"], "behind": lead - row["points"],
                              "season": table.get("season_label") or ""})
        last = next((r for r in table.get("last_results") or [] if follow.matches(name, r["name"])), None)
        if last:
            results.append({"series": sid, "event": table.get("last_event"), "session": table.get("last_session"),
                            "pos": last["pos"], "time": last.get("time"), "points": last.get("points")})
    return {"name": name, "series": st["series"], "found": st["found"], "rides": rides, "next": st["next"],
            "new": st["new"], "upcoming": upcoming[:8], "standings": standings, "results": results}


@app.get("/api/names")
def names():
    return live.tagger().directory()


def _since(days: float) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


@app.get("/api/wire")
def wire(request: Request, kind: str | None = None, series: str | None = None, driver: str | None = None,
         team: str | None = None, mine: bool = False):
    """News and the important race events, newest first, with the Wire's filters."""
    items = []
    if kind in (None, "", "news"):
        items += [{**n, "type": "news"} for n in store.news()]
    if kind in (None, "", "events"):
        items += [{**e, "type": "event", "series": [e["series"]]} for e in store.events_since(_since(30), important=True, limit=400)]
    if series:
        wanted = set(series.split(","))
        items = [i for i in items if wanted & set(i["series"])]
    if driver:
        items = [i for i in items if driver in i["drivers"]]
    if team:
        items = [i for i in items if team in i["teams"]]
    if mine:
        st = store.settings(request.state.email) or {}
        f = {follow.normalise(x["name"]) for x in st.get("follows") or []}
        items = [i for i in items if alerts.matches(f, i["drivers"] + i["teams"])]
    items.sort(key=lambda i: i["time"], reverse=True)
    return items[:200]


@app.get("/api/events/{series_id}")
def live_events(series_id: str, hours: float = 8):
    """Everything from the current weekend's sessions, for the live screen's Events panel."""
    if series_id not in BY_ID:
        raise HTTPException(404)
    return store.events_since(_since(hours / 24), series=[series_id], limit=300)


@app.get("/api/story/{series_id}/{event_key}")
def race_story(series_id: str, event_key: str):
    if series_id not in BY_ID:
        raise HTTPException(404)
    return store.events_since("2000-01-01T00:00:00Z", series=[series_id], event_key=event_key, limit=2000)


@app.get("/api/stories/{series_id}")
def stories(series_id: str):
    """Which of a series' events have a Race story to show."""
    rows = store.conn.execute("SELECT DISTINCT event_key FROM race_event WHERE series=? AND event_key IS NOT NULL",
                              (series_id,))
    return [r[0] for r in rows]


# ---- web push ---------------------------------------------------------------------------
@app.get("/api/push")
def push_info(request: Request):
    return {"key": pusher.public_key, "devices": len(store.pushes(request.state.email))}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    body = await request.json()
    sub = body.get("subscription") or {}
    if not str(sub.get("endpoint", "")).startswith("https://") or not (sub.get("keys") or {}).get("p256dh"):
        raise HTTPException(400)
    store.add_push(request.state.email, sub, request.headers.get("user-agent"))
    return {"ok": True}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    body = await request.json()
    store.remove_push(str(body.get("endpoint") or ""), request.state.email)
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(request: Request):
    subs = store.pushes(request.state.email)
    n = await alerter.send(subs, {"title": "Lights Out", "body": "Alerts work on this device.",
                                  "url": f"{access.BASE_URL}/#/settings", "tag": "test"})
    return {"sent": n, "devices": len(subs)}


@app.get("/")
def index():
    v = _asset_version()
    page = (WEB_DIR / "index.html").read_text()
    page = page.replace('src="app.js"', f'src="app.js?v={v}"').replace('href="style.css"', f'href="style.css?v={v}"')
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
