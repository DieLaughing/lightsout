"""Lights Out web server: JSON API, calendar feeds and the web app."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import logging
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from . import access, bugs, adapters, alerts, changes, colors, f1a, follow, ics, news, odds, picks, places, profiles, refresh, slow, tickets, usage, watch, weather
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
bugs.init(store.conn)
picks.init(store.conn)
usage.init(sqlite3.connect(store.path, check_same_thread=False, timeout=10))   # its own: see usage.init
slow.init(sqlite3.connect(store.path, check_same_thread=False, timeout=10))
CAL_TOKEN = _cal_token()
live = LiveHub(store)
profiles.setup(DATA_DIR)
odds.setup(DATA_DIR)
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
                  asyncio.create_task(tickets.refresh_loop()), asyncio.create_task(weather.loop(store, DATA_DIR)),
                  asyncio.create_task(slow.loop(store, access.notify)), asyncio.create_task(profiles.loop(store))]
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
             "has_standings": adapters.has_standings(s.id, s.adapter)} for s in SERIES]


@app.get("/api/sessions")
def list_sessions(start: str | None = None, end: str | None = None, series: str | None = None):
    now = datetime.now(timezone.utc)
    start = start or _iso(now - timedelta(hours=12))
    end = end or _iso(now + timedelta(days=60))
    ids = [s for s in series.split(",") if s in BY_ID] if series else None
    return places.tag(weather.attach(_with_watch(store.sessions(start, end, ids))))


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
        weather.attach([{**s, "venue": e.get("venue"), "event_name": e.get("name"), "_s": s} for s in e["sessions"]])
    return places.tag(events)


@app.get("/api/series/{series_id}/standings")
def series_standings(series_id: str):
    if series_id not in BY_ID:
        raise HTTPException(404)
    table = store.standings(series_id)
    if table and series_id == "f1a":
        for rows in (table.get("drivers") or [], table.get("last_results") or [], *(table.get("qualifying") or {}).values()):
            f1a.tag(store, rows)
    return colors.tint(table, series_id) if table else {}


@app.get("/api/live")
def live_summary():
    return live.summary()


@app.get("/api/live/{series_id}")
def live_series(series_id: str):
    snap = live.snapshot(series_id) or {}
    if series_id == "f1a" and snap.get("rows"):
        f1a.tag(store, snap["rows"], colour=True)
    return snap


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


RACE_KINDS = {RACE, SPRINT, RALLY_DAY, EVENT}  # what "Races only" keeps (web/app.js RACE_KINDS)


def shows(kind: str, level: str | None) -> bool:
    """A series' "On Upcoming" level (settings.show): absent = everything, qualifying = qualifying + races."""
    return not level or kind in RACE_KINDS or (level == "qualifying" and kind == QUALIFYING)


@app.get("/cal/{token}/{codes}.ics")
def calendar(token: str, codes: str, request: Request, kinds: str | None = None, show: str | None = None):
    if not secrets.compare_digest(token, CAL_TOKEN):
        raise HTTPException(404)
    ids = [c for c in codes.split("+") if c in BY_ID]
    if not ids:
        raise HTTPException(404)
    wanted = set(kinds.split(",")) & DEFAULT_KINDS if kinds else DEFAULT_KINDS
    levels = dict(x.split(":", 1) for x in (show or "").split(",") if ":" in x)  # "wec:qualifying,cup:races"
    now = datetime.now(timezone.utc)
    rows = [r for r in store.sessions(_iso(now - timedelta(days=30)), _iso(now + timedelta(days=400)), ids)
            if r["kind"] in wanted and shows(r["kind"], levels.get(r["series"]))]
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


# Finished answers for the heavy GETs that are the same for everyone (the Wire: per person, it reads their follows).
# Built and compressed once, reused for READY_TTL; live timing is never kept.
READY_TTL = 30.0
READY_PATHS = ("/api/series", "/api/sessions", "/api/names", "/api/entries", "/api/event/", "/api/stories/", "/api/track",
               "/api/person/", "/api/wire", "/api/changes")  # not /api/live, /api/events/, /api/story/: those move during races
_ready: dict[tuple, tuple] = {}


def _ready_key(request: Request) -> tuple | None:
    path = request.url.path
    if request.method != "GET" or not path.startswith(READY_PATHS):
        return None
    return path, request.url.query, request.state.email if path == "/api/wire" else ""


def _ready_response(hit: tuple, request: Request) -> Response:
    _, raw, gz = hit
    if gz and "gzip" in request.headers.get("accept-encoding", ""):
        return Response(gz, media_type="application/json", headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(raw, media_type="application/json")


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
    key = _ready_key(request)
    if key and (hit := _ready.get(key)) and time.monotonic() - hit[0] < READY_TTL:
        return _ready_response(hit, request)
    started = time.perf_counter()
    response = await call_next(request)
    if path.startswith("/api/") and (took := time.perf_counter() - started) > slow.SLOW:
        log.warning("slow request: %s %s %.0f ms", request.method, path, took * 1000)
        route = getattr(request.scope.get("route"), "path", None) or "(no route)"   # /api/live/{series_id}, not each series
        asyncio.get_running_loop().run_in_executor(None, slow.add, route, round(took * 1000), any(f.running for f in live.feeds.values()))
    if key and response.status_code == 200:
        raw = b"".join([chunk async for chunk in response.body_iterator])
        if len(_ready) > 300:
            _ready.clear()
        _ready[key] = hit = (time.monotonic(), raw, gzip.compress(raw, 6) if len(raw) > 1500 else None)
        return _ready_response(hit, request)
    if not path.startswith(("/api/", "/cal/")):
        # Without this, Cloudflare caches .js/.css for hours and new versions don't show up. Icons barely change (and the
        # page asks for them with ?v=), so a day's cache saves a round trip per navigation on the favicon.
        response.headers.setdefault("Cache-Control", "public, max-age=86400" if path.startswith("/icons/") else "no-cache")
    return response


# Added after gate() so it wraps it: compresses what gate() hands back, and leaves the already-gzipped cached
# answers alone (they carry Content-Encoding).
app.add_middleware(GZipMiddleware, minimum_size=1500)  # JSON shrinks ~5x on the way out


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))



# ---- bug reports (filed as GitHub issues, anonymously; see bugs.py) ------------------------------------
@app.post("/api/bug")
async def bug_report(request: Request):
    body = await request.json()
    kind = body.get("kind") if body.get("kind") in bugs.KINDS else "bug"
    text = str(body.get("text") or "").strip()[:4000]
    if len(text) < 10:
        raise HTTPException(400, "Tell us a bit more (at least a sentence).")
    page = str(body.get("page") or "")[:200]
    device = str(body.get("device") or "")[:120]
    version = (changes.load() or [{}])[0].get("date", "")
    email = request.state.email
    if not bugs.configured():
        return {"link": bugs.new_issue_link(kind, text, page, device, version)}
    if not access.is_admin(email) and bugs.sent_today(email) >= bugs.PER_DAY:
        raise HTTPException(429, "That's a lot of reports for one day. Thanks! Try again tomorrow.")
    try:
        out = await bugs.file(email, kind, text, page, device, version)
    except Exception as exc:
        # An expired or revoked token shouldn't lose the report: hand over GitHub's own form and tell the owner.
        log.warning("filing a bug report failed: %r", exc)
        await access.notify("Bug report couldn't be filed", f"GitHub said: {exc!r}"[:300] + " (check the GitHub token)", tags="warning")
        return {"link": bugs.new_issue_link(kind, text, page, device, version)}
    await access.notify(f"Lights Out: new {bugs.KINDS[kind][0].lower()} #{out['issue']}", out["title"],
                        tags="bug", click=out["url"])
    return out


@app.get("/api/bugs")
async def my_bug_reports(request: Request):
    return {"reports": await bugs.mine(request.state.email), "repo": bugs.REPO, "filing": bugs.configured()}

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
    inv = store.invite(code)
    # A group link mustn't undo a decision about this person; a link made for them alone still can.
    shut_out = user and user["status"] in ("denied", "revoked") and inv and inv["max_uses"] > 1
    if shut_out or not store.use_invite(code, email):
        return HTMLResponse("<p>This invite link is used up or doesn't exist. "
                            "<a href='/'>Request access instead</a>.</p>", status_code=410)
    store.set_status(email, "approved")
    joined = f" ({len(inv['uses']) + 1} of {inv['max_uses']})" if inv["max_uses"] > 1 else ""
    note = f" \"{inv['note']}\"" if inv["note"] else ""
    await access.notify("Lights Out: invite used", f"{email} joined with the invite link{note}{joined}.", tags="tada")
    return RedirectResponse("/")


@app.get("/api/admin")
def admin_overview():
    return {"users": [{k: u[k] for k in ("email", "status", "requested", "decided")} for u in store.users()],
            "invites": [{**i, "link": f"{access.BASE_URL}/invite/{i['code']}"} for i in store.invites()],
            "usage": usage.summary()}


@app.get("/api/admin/usage")
def admin_usage(since: str | None = None):
    """Raw page-view counts per day and page (the monthly idea session reads these)."""
    try:
        since and datetime.strptime(since, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "since must be YYYY-MM-DD")
    return {"days": usage.days(since), "keep_days": usage.KEEP_DAYS}


# ---- page views (anonymous counts per page and day; see usage.py) ------------------------------
@app.post("/api/views")
async def page_views(request: Request):
    """A phone's batch of page views ({views: {route: count}}). The owner's own views aren't counted."""
    if access.is_admin(request.state.email):
        return Response(status_code=204)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "bad JSON")
    views = body.get("views") if isinstance(body, dict) else None
    if isinstance(views, dict):
        await asyncio.to_thread(usage.add, views, request.state.email)   # a SQLite write: off the event loop
    return Response(status_code=204)


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
    try:
        uses = min(max(int(body.get("uses") or 1), 1), 50)
    except (TypeError, ValueError):
        raise HTTPException(400, "uses must be a number")
    store.add_invite(code, (body.get("note") or "")[:80] or None, uses)
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
    if "units" in body:
        clean["units"] = "c" if body["units"] == "c" else "f"  # weather temperatures
    if "seenSeries" in body:
        clean["seenSeries"] = [x for x in body["seenSeries"] or [] if x in BY_ID]
    if "kinds" in body:
        clean["kinds"] = [k for k in body["kinds"] or [] if k in DEFAULT_KINDS]
    if "dismissed" in body:
        clean["dismissed"] = [str(x)[:200] for x in (body["dismissed"] or [])][-500:]
    if "notify" in body:
        clean["notify"] = {k: bool((body["notify"] or {}).get(k, True)) for k in alerts.KINDS}
    if "odds" in body:
        clean["odds"] = body["odds"] is not False  # betting odds on event, driver and Pick'em pages (Settings > Display)
    if "animations" in body:
        clean["animations"] = body["animations"] is not False  # page transitions (Settings > Display)
    if "show" in body:  # per-series "Show on Upcoming": absent = everything
        clean["show"] = {k: v for k, v in (body["show"] or {}).items() if k in BY_ID and v in ("qualifying", "races")}
    if "home" in body:  # "Near me": {label, lat, lon}, rounded to ~1 km; null clears it
        h = body["home"]
        try:
            lat, lon = round(float(h["lat"]), 2), round(float(h["lon"]), 2)
            clean["home"] = {"label": str(h.get("label") or "Home")[:40], "lat": lat, "lon": lon} \
                if -90 <= lat <= 90 and -180 <= lon <= 180 else None
        except (TypeError, KeyError, ValueError):
            clean["home"] = None
    if "nearMiles" in body:
        clean["nearMiles"] = body["nearMiles"] if body["nearMiles"] in (100, 200, 300, 500, 1000) else 300
    if "nickname" in body:  # the name friends see on Pick'em; empty = the email's first part
        clean["nickname"] = " ".join("".join(c for c in str(body["nickname"] or "") if c.isprintable()).split())[:24]
    store.save_settings(request.state.email, clean)
    return clean


@app.get("/api/person/{name}")
def person(name: str):
    """Everything known about one followed name (driver or team): where they race, what's next, this season's
    cards (as on Home), their last result. News and race events come from /api/wire?driver=."""
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
    weather.attach(_with_watch(upcoming))
    results = []
    tables = {sid: store.standings(sid) or {} for sid in st["series"]}
    for sid, table in tables.items():
        last = next((r for r in table.get("last_results") or [] if follow.matches(name, r["name"])), None)
        if last:
            results.append({"series": sid, "event": table.get("last_event"), "session": table.get("last_session"),
                            "pos": last["pos"], "time": last.get("time"), "points": last.get("points")})
    # Their line in the bookmaker's odds for each series' next race weekend.
    today = _iso(now)[:10]
    lines = []
    for sid in dict.fromkeys(r["series"] for r in rides):
        ev = next((e for e in store.events(sid) if (e.get("end_day") or e.get("start_day") or "") >= today), None)
        mine = odds.for_driver(odds.for_event(sid, ev.get("start_day"), ev.get("end_day")), name) if ev else []
        if mine:
            lines.append({"series": sid, "event": ev["name"], "event_key": ev["key"], "lines": mine})
    # An F1 team's page lists the F1 ACADEMY drivers racing in its colours.
    academy = [{"name": d["name"], "team": d["team"], "number": d["number"]} for d in f1a.roster(store)
               if (team := f1a.f1_team(d["backer"])) and follow.normalise(team) == follow.normalise(name)]
    return {"name": name, "series": st["series"], "found": st["found"], "rides": rides, "next": st["next"], "academy": academy, "odds": lines,
            "new": st["new"], "upcoming": upcoming[:8], "results": results,
            "cards": [c for c in (_card(name, sid, t) for sid, t in tables.items()) if c]}


@app.get("/api/profile/{name}")
async def profile(name: str):
    """A driver's photo, Wikipedia lines and F1 career, from what's saved; pending=True while it's being built
    (the page asks again). Never waits on Wikipedia or Jolpica."""
    return profiles.get(store, name.strip()[:80])


@app.get("/img/d/{file}")
def driver_photo(file: str):
    path = profiles.photo_path(file)
    if not path or not path.exists():
        raise HTTPException(404)
    media = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix, "image/jpeg")
    # The name is the photo's own hash: a new photo gets a new name, so phones and Cloudflare may keep this for good.
    return Response(path.read_bytes(), media_type=media, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/cards")
def cards(request: Request):
    """Home-page cards for everyone you follow: one per championship they're in (a driver row, else a team row),
    with points, the gap to the leader, wins when the series publishes them and season form from round_result."""
    names = [f["name"] for f in (store.settings(request.state.email) or {}).get("follows") or [] if f.get("name")]
    out, tables = [], {}  # each standings table read once per request
    for name, st in zip(names, follow.status(store, names) if names else []):
        mine = [c for c in (_card(name, sid, tables.setdefault(sid, store.standings(sid) or {})) for sid in st["series"]) if c]
        # A one-off outside their championship (a Cup driver's O'Reilly start: P69, 0 points) is noise next to a real card.
        scored = [c for c in mine if c["points"] > 0]
        out += sorted(scored or mine[:1], key=lambda c: c["pos"])  # best championship first (points scales differ)
    return out


def _card(name: str, sid: str, table: dict) -> dict | None:
    groups = (("driver", table.get("drivers") or []),
              ("class", [r for v in (table.get("classes") or {}).values() for r in v]),
              ("team", table.get("teams") or []))
    kind, row = next(((k, r) for k, rows in groups for r in rows if follow.matches(name, r["name"])), (None, None))
    if not row:
        return None
    team = row["name"] if kind == "team" else row.get("team") or ""
    # Gap to the leader of the same table (a class row's leader isn't the overall leader, so no gap there).
    lead = {"driver": table.get("drivers"), "team": table.get("teams")}.get(kind)
    form = []
    if kind != "team":  # a team's round would be two cars; drivers only
        for r in store.rounds(sid, refresh.season_of(table.get("season_label", ""))):
            key = row["name"] if row["name"] in r["rows"] else next((k for k in r["rows"] if follow.matches(row["name"], k)), None)
            form.append({"event": r["event"], "pos": r["rows"][key][0] if key else "",
                         "points": r["rows"][key][1] if key else 0})
    return {"name": name, "row": row["name"], "kind": kind, "series": sid, "team": "" if kind == "team" else team,
            "color": colors.for_team(team, sid), "pos": row["pos"], "points": row["points"],
            "behind": lead[0]["points"] - row["points"] if lead and row["pos"] > 1 else None,
            "wins": row.get("wins"), "form": form, "season": table.get("season_label", "")}


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


# ---- one race weekend ------------------------------------------------------------------------------
GENERIC = {"grand", "prix", "race", "rally", "rallye", "hours", "of", "the", "e", "fia", "round", "series", "championship",
           "nascar", "craftsman", "truck", "cup", "world", "and", "at", "de", "du", "del", "d", "endurance", "sprint", "6", "8",
           "24", "1000", "km", "test", "official", "united", "states", "city", "international", "circuit", "speedway",
           "raceway", "motor", "street", "autodromo", "park", "o", "reilly", "auto", "parts", "presented", "by", "500", "400",
           "des", "la", "le", "lone", "star", "new", "san", "santa"}


def _shift(day: str, days: int) -> str:
    return (datetime.fromisoformat(day) + timedelta(days=days)).date().isoformat()


def _keywords(ev: dict) -> set[str]:
    words = set(weather.norm(f"{ev.get('name')} {ev.get('venue')}").split())
    place = weather.place_for(ev.get("venue"), ev.get("name"))
    for term in (place or {}).get("terms", []):
        words |= set(term.split())
    return {w for w in words - GENERIC if len(w) > 2 and not w.isdigit()}


@app.get("/api/event/{series_id}/{event_key}")
def event_page(series_id: str, event_key: str):
    """Everything about one race weekend: sessions (watch, tickets, weather), round number, entry list, results,
    Race story, other series racing at the same place, and news that mentions it."""
    if series_id not in BY_ID:
        raise HTTPException(404)
    events = store.events(series_id)
    ev = next((e for e in events if e["key"] == event_key), None)
    if not ev:
        raise HTTPException(404)
    f1_events = _f1_calendar() if series_id in tickets.F1_FAMILY else []
    ev["tickets"] = tickets.for_event(series_id, ev, f1_events)
    for x in ev["sessions"]:
        x["watch"] = watch.watch_for(series_id, x["kind"], x.get("tv"))
    weather.attach([{**x, "venue": ev.get("venue"), "event_name": ev.get("name"), "_s": x} for x in ev["sessions"]])
    places.tag([ev])
    season = [e for e in events if e["season"] == ev["season"]]
    ev["round"], ev["rounds"] = season.index(ev) + 1, len(season)

    entered = [r["name"] for r in store.conn.execute(
        "SELECT name FROM event_entry WHERE series=? AND event_key=? ORDER BY name", (series_id, event_key))]
    grid = {r["name"]: r for r in store.entries() if r["series"] == series_id}
    ev["entry_list"] = bool(entered)
    ev["drivers"] = [{"name": n, "team": (grid.get(n) or {}).get("team"), "number": (grid.get(n) or {}).get("number")}
                     for n in (entered or sorted(grid, key=lambda n: (grid[n].get("team") or "", n)))]
    if series_id == "f1a":
        f1a.tag(store, ev["drivers"])
        if (ev.get("end_day") or ev.get("start_day") or "") >= _iso(datetime.now(timezone.utc))[:10]:
            ev["watch_list"] = f1a.watch(store, ev, weather.place_for(ev.get("venue"), ev.get("name")))
    ev["odds"] = odds.for_event(series_id, ev.get("start_day"), ev.get("end_day"))

    st = store.standings(series_id) or {}
    same = st.get("last_event") and weather.norm(st["last_event"]) in (weather.norm(ev["name"]), weather.norm(ev.get("venue") or ""))
    ev["results"] = {"session": st.get("last_session"), "rows": st.get("last_results")} if same else None
    q_same = st.get("quali_event") and weather.norm(st["quali_event"]) in (weather.norm(ev["name"]), weather.norm(ev.get("venue") or ""))
    ev["qualifying"] = colors.tint({"qualifying": st.get("qualifying")}, series_id)["qualifying"] if q_same else None
    ev["story"] = bool(store.conn.execute("SELECT 1 FROM race_event WHERE series=? AND event_key=? LIMIT 1",
                                          (series_id, event_key)).fetchone())

    place = weather.place_for(ev.get("venue"), ev.get("name"))
    ev["weekend"] = []
    if place and ev.get("start_day"):
        for other in SERIES:
            if other.id == series_id:
                continue
            for e in store.conn.execute("SELECT key, name, venue, start_day, end_day FROM event WHERE series=? "
                                        "AND start_day <= ? AND COALESCE(end_day, start_day) >= ?",
                                        (other.id, _shift(ev.get("end_day") or ev["start_day"], 1), _shift(ev["start_day"], -1))):
                if (weather.place_for(e["venue"], e["name"]) or {}).get("name") == place["name"]:
                    ev["weekend"].append({"series": other.id, "key": e["key"], "name": e["name"]})

    words = _keywords(ev)
    lo = (datetime.fromisoformat(ev["start_day"]) - timedelta(days=10)).isoformat() if ev.get("start_day") else ""
    hi = (datetime.fromisoformat(ev.get("end_day") or ev["start_day"]) + timedelta(days=4)).isoformat() if ev.get("start_day") else "9"
    ev["news"] = [n for n in store.news() if series_id in n["series"] and lo <= n["time"][:10] <= hi[:10]
                  and words & set(weather.norm(f"{n['title']} {n.get('text') or ''}").split())][:8]
    return ev


@app.get("/api/track/{slug}")
def track_page(slug: str):
    """One track: where it is, every series racing there this season and next, and its sessions in the next 2 weeks."""
    p = places.by_slug(slug)
    if not p:
        raise HTTPException(404)
    year = datetime.now(timezone.utc).year
    f1_events = None  # read only when an F1-family weekend is here (its ticket links come from the F1 calendar)
    events = []
    for e in store.conn.execute("SELECT series, key, name, venue, country, start_day, end_day, season FROM event "
                                "WHERE season >= ? ORDER BY start_day", (year,)):
        e = dict(e)
        if e["series"] in BY_ID and (weather.place_for(e["venue"], e["name"]) or {}).get("name") == p["name"]:
            if (e.get("end_day") or e.get("start_day") or "") >= _iso(datetime.now(timezone.utc))[:10]:
                if f1_events is None and e["series"] in tickets.F1_FAMILY:
                    f1_events = _f1_calendar()
                e["tickets"] = tickets.for_event(e["series"], e, f1_events or [])
            events.append(e)
    now = datetime.now(timezone.utc)
    soon = [r for r in store.sessions(_iso(now - timedelta(hours=12)), _iso(now + timedelta(days=14)))
            if (weather.place_for(r["venue"], r["event_name"]) or {}).get("name") == p["name"]]
    return {**places.info(p), "events": events, "sessions": places.tag(weather.attach(_with_watch(soon)))}


@app.get("/api/tracks")
def track_list():
    """Every track with a race this season or later: where it is and its next event (any series), for "Near me"."""
    today = _iso(datetime.now(timezone.utc))[:10]
    out: dict[str, dict] = {}
    for e in store.conn.execute("SELECT series, key, name, venue, start_day, end_day FROM event WHERE season >= ? "
                                "ORDER BY start_day", (datetime.now(timezone.utc).year,)):
        p = weather.place_for(e["venue"], e["name"])
        if not p or e["series"] not in BY_ID:
            continue
        t = out.setdefault(p["name"], {**places.info(p), "next": None, "series": []})
        if e["series"] not in t["series"]:
            t["series"].append(e["series"])
        if not t["next"] and (e["end_day"] or e["start_day"] or "") >= today:
            t["next"] = {k: e[k] for k in ("series", "key", "name", "start_day", "end_day")}
    return list(out.values())


@app.get("/api/home")
async def home_lookup(zip: str = ""):
    """A US ZIP code -> {label, lat, lon} for "Near me" (rounded; the app saves it in the person's settings)."""
    found = await places.zip_home(zip)
    if not found:
        raise HTTPException(404, "Unknown ZIP code")
    return found


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


# ---- Pick'em (lightsout/picks.py) -------------------------------------------------------------------
def friend_names() -> dict[str, str]:
    """Everyone approved: email -> the name friends see. Emails never leave the server."""
    return {e: (st.get("nickname") or e.split("@")[0]) for e, st in store.approved_settings().items()}


@app.get("/api/pickem")
def pickem(request: Request, series: str = "f1", event: str | None = None):
    if series not in picks.SERIES:
        raise HTTPException(404)
    me, names = request.state.email, friend_names()
    picks.grade(store, series)
    races = picks.races(store, series)
    race = picks.choose(races, event)
    out = {"series": series, "choices": list(picks.SERIES), "name": names.get(me, me.split("@")[0]),
           "race": None, "slate": None, "board": None, "prev": None, "next": None}
    if not race:
        return out
    i = races.index(race)
    out["prev"] = races[i - 1]["event_key"] if i else None
    out["next"] = races[i + 1]["event_key"] if i + 1 < len(races) else None
    season = [r for r in races if r["season"] == race["season"]]
    out["race"] = {**race, "round": season.index(race) + 1, "rounds": len(season),
                   "opens": picks._iso(picks._parse(race["start"]) - timedelta(days=picks.OPEN_DAYS))}
    sl = picks.slate(store, series, race)
    out["slate"] = picks.view(store, me, sl, names) if sl else None
    ev = next((e for e in store.events(series) if e["key"] == race["event_key"]), None)
    book = odds.for_event(series, ev.get("start_day"), ev.get("end_day")) if ev and out["slate"] else None
    if book:  # the bookmaker's line that best tells each duel's drivers apart (odds.duel)
        for x in out["slate"]["duels"]:
            pair = odds.duel(book, x["drivers"][0]["name"], x["drivers"][1]["name"])
            for d, line in zip(x["drivers"], pair or (None, None)):
                d["odds"] = line
        out["odds_source"] = book["source"]
    out["board"] = picks.board(store, series, race["season"], names, me)
    return out


@app.post("/api/pickem")
async def pickem_save(request: Request):
    body = await request.json()
    series = body.get("series")
    if series not in picks.SERIES:
        raise HTTPException(400, "Unknown series.")
    race = picks.choose(picks.races(store, series), str(body.get("event") or "")) if body.get("event") else None
    sl = picks.slate(store, series, race) if race else None
    if not sl:
        raise HTTPException(400, "Picks aren't open for this race.")
    try:
        slot = int(body.get("slot"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Unknown pick.")
    try:
        picks.set_pick(store, request.state.email, sl, str(body.get("kind")), slot,
                       str(body["name"])[:80] if body.get("name") else None, bool(body.get("lock")))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


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
