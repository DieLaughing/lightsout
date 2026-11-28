"""The Dakar Rally, stage by stage: the stage list, each stage's top three and the overall top three per class, the
final top ten, stage alerts and a stage-winner Pick'em.

Results come from the English Wikipedia article "<year> Dakar Rally" (CC BY-SA, credited wherever they show). Its
editors post each stage's results within hours, in the same tables every year (Stages, Stage winners, Stage
results per class, Final standings). dakar.com isn't used: it refuses automated requests.

The article is read every POLL seconds while the rally runs (a cheap revision check first, the page only when it
changed), daily in the weeks before it, and last year's finished article once, for the Pick'em field.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from .adapters.base import get_json
from .alerts import matches
from .follow import normalise

log = logging.getLogger("lightsout.dakar")

SERIES = "w2rc"
WIKI = "https://en.wikipedia.org/w/api.php"
HEADERS = {"Api-User-Agent": "LightsOut/1.0 (https://github.com/DieLaughing/lightsout)"}
POLL = 15 * 60
BEFORE_DAYS = 45            # read the article daily from this long before the start (stages, entry news)
AFTER_DAYS = 3              # and every POLL until this long after the finish (late corrections)
PICK_CLASSES = ("Bikes", "Cars")
ALERT_CLASSES = PICK_CLASSES
CLOSE_UTC = "05:00"         # stage picks close at 06:00 Morocco time (UTC+1) on the stage's day
PICKS_FROM_DAYS = 14        # the Dakar tab shows in Pick'em from two weeks before the start
PICKS_UNTIL_DAYS = 10       # ...until ten days after the finish
FIELD = 25                  # names to pick from per class

SCHEMA = """
CREATE TABLE IF NOT EXISTS dakar_pick (
    email TEXT NOT NULL, year INTEGER NOT NULL, stage TEXT NOT NULL, class TEXT NOT NULL,
    name TEXT NOT NULL, created TEXT NOT NULL,
    PRIMARY KEY (email, year, stage, class)
);
"""


def init(conn) -> None:
    with conn:
        conn.executescript(SCHEMA)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def key(year: int) -> str:
    return f"dakar:{year}"


def title(year: int) -> str:
    return f"{year} Dakar Rally"


def article_url(year: int) -> str:
    return f"https://en.wikipedia.org/wiki/{quote(title(year).replace(' ', '_'))}"


# ---- wikitext -----------------------------------------------------------------------------------
def _split_top(s: str, sep: str) -> list[str]:
    """Split on `sep` outside [[links]] and {{templates}}."""
    out, depth, cur, i = [], 0, "", 0
    while i < len(s):
        two = s[i:i + 2]
        if two in ("[[", "{{"):
            depth, cur, i = depth + 1, cur + two, i + 2
            continue
        if two in ("]]", "}}"):
            depth, cur, i = max(0, depth - 1), cur + two, i + 2
            continue
        if depth == 0 and s.startswith(sep, i):
            out.append(cur)
            cur, i = "", i + len(sep)
            continue
        cur, i = cur + s[i], i + 1
    return out + [cur]


def _cell(raw: str) -> dict:
    """One table cell: its attributes (rowspan/colspan) and text."""
    parts = _split_top(raw, "|")
    attrs, text = ("", raw) if len(parts) == 1 or "=" not in parts[0] or "[[" in parts[0] or "{{" in parts[0] else (parts[0], "|".join(parts[1:]))
    span = lambda name: int(m.group(1)) if (m := re.search(rf'{name}\s*=\s*"?(\d+)', attrs)) else 1  # noqa: E731
    flag = re.search(r"(?i)\{\{\s*flag(?:icon|athlete)?\s*\|\s*([A-Z]{3})", text)
    return {"text": clean(text), "rowspan": span("rowspan"), "colspan": span("colspan"), "country": flag.group(1) if flag else ""}


def clean(s: str) -> str:
    s = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"\{\{\s*(?:nowrap|small|nobr)\s*\|([^{}]*)\}\}", r"\1", s, flags=re.I)
    while re.search(r"\{\{[^{}]*\}\}", s):
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"<[^>]+>|'{2,}", "", s).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


def tables(text: str) -> list[list[dict]]:
    """Every {| table |} in `text` as a list of rows; a row is its cells in order ({text, rowspan, colspan, country,
    head}), header (!) cells with head=True."""
    out = []
    for t in re.findall(r"^\{\|.*?^\|\}", text, re.M | re.S):
        rows, row = [], None
        for line in t.splitlines()[1:]:
            line = line.rstrip()
            if line.startswith("|-") or line.startswith("|}"):
                if row:
                    rows.append(row)
                row = [] if line.startswith("|-") else None
                continue
            if line.startswith("|+"):
                continue
            if line[:1] in ("|", "!"):
                if row is None:
                    row = []
                head = line[0] == "!"
                for c in _split_top(line[1:], "!!" if head else "||"):
                    row.append({**_cell(c), "head": head})
            elif row:   # a cell that runs on over several lines
                last = row[-1]
                row[-1] = {**last, "text": clean(f"{last['text']} {line}")}
        if row:
            rows.append(row)
        out.append([r for r in rows if r])
    return out


def sections(text: str, level: int) -> dict[str, str]:
    """Heading -> its text (up to the next heading of the same or a higher level)."""
    marks = [(m.start(), m.end(), len(m.group(1)), m.group(2).strip())
             for m in re.finditer(r"^(=+)\s*(.*?)\s*=+\s*$", text, re.M)]
    out = {}
    for i, (_, end, lv, name) in enumerate(marks):
        if lv != level:
            continue
        stop = next((s for s, _, l2, _ in marks[i + 1:] if l2 <= level), len(text))
        out[name] = text[end:stop]
    return out


def stage_label(s: str) -> str:
    """'4 (marathon)', 'Stage 4' and '4' are all 'Stage 4'; 'Rest Day' is 'Rest day'."""
    s = clean(s)
    m = re.match(r"(?i)(?:stage\s*)?(\d+)\b", s)
    if m:
        return f"Stage {int(m.group(1))}"
    return s[:1].upper() + s[1:].lower() if s else s


def stage_note(s: str) -> str:
    m = re.search(r"\(([^)]*)\)", clean(s))
    return m.group(1).strip().lower() if m else ""


def _day(s: str) -> str | None:
    try:
        return datetime.strptime(clean(s), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def _km(s: str) -> int | None:
    m = re.match(r"[\d,]+", s.replace(" ", ""))
    return int(m.group(0).replace(",", "")) if m else None


def _kms(cells: list[dict]) -> tuple[int | None, int | None]:
    """Special and total km. Tables give one column each (FIM & FIA alike) or two each (FIM, FIA), and a two-column
    pair may be one cell spanning both: the total starts halfway along the columns."""
    cols = []
    for c in cells:
        cols += [c] * c["colspan"]
    if not cols:
        return None, None
    half = len(cols) // 2 if len(cols) >= 4 else 1
    return _km(cols[0]["text"]), _km(cols[half]["text"]) if len(cols) > half else None


def parse_stages(text: str) -> list[dict]:
    """The Stages table: stage, date, start, finish, special km, total km. Rest days and transfers kept (kind).
    A place cell spanning two columns is start and finish (rest days)."""
    found = tables(sections(text, 2).get("Stages") or "")
    out = []
    for row in found[0] if found else []:
        at = next((i for i, c in enumerate(row) if _day(c["text"])), None)
        if at is None or row[0]["head"]:
            continue
        day, rest, first = _day(row[at]["text"]), row[at + 1:], row[0]["text"]
        label = stage_label(first)
        kind = "rest" if "rest" in label.lower() else "transfer" if "transfer" in label.lower() else "stage"
        both = bool(rest) and rest[0]["colspan"] > 1
        start = rest[0]["text"] if rest else ""
        finish = start if both else rest[1]["text"] if len(rest) > 1 else ""
        special, total = _kms(rest[1:] if both else rest[2:])
        out.append({"label": label, "note": stage_note(first), "kind": kind, "day": day, "start": start, "finish": finish,
                    "special_km": special if kind == "stage" else None, "total_km": total if kind == "stage" else None})
    return out


def _result_row(cells: list[dict]) -> dict | None:
    if len(cells) < 4 or not re.fullmatch(r"\d+", cells[0]["text"]):
        return None
    gap = cells[4]["text"] if len(cells) > 4 else ""
    return {"pos": int(cells[0]["text"]), "name": cells[1]["text"], "country": cells[1]["country"],
            "make": cells[2]["text"], "time": cells[3]["text"], "gap": gap.lstrip("+") if gap else ""}


def parse_results(text: str) -> dict[str, dict[str, dict]]:
    """Stage results per class: {class: {stage label: {"stage": [rows], "overall": [rows]}}}. Each table row is the
    stage's Nth place then the overall Nth place; a stage that doesn't count overall (the prologue, some years) has a
    note there instead."""
    part = sections(text, 2).get("Stage results") or ""
    out: dict[str, dict[str, dict]] = {}
    for cls, body in sections(part, 3).items():
        found = tables(body)
        if not found:
            continue
        stages, current = {}, None
        for row in found[0]:
            first = row[0]
            if first["head"] and (first["rowspan"] > 1 or re.match(r"(?i)(stage|prologue)", first["text"])) and first["text"]:
                current = stage_label(first["text"])
                cells = row[1:]
            elif first["head"]:
                continue
            else:
                cells = row
            if not current:
                continue
            stage, overall = _result_row(cells[:5]), _result_row(cells[5:10]) if len(cells) >= 9 else None
            slot = stages.setdefault(current, {"stage": [], "overall": []})
            if stage:
                slot["stage"].append(stage)
            if overall:
                slot["overall"].append(overall)
        out[cls] = {k: v for k, v in stages.items() if v["stage"] or v["overall"]}
    return out


def parse_winners(text: str) -> dict[str, dict[str, str]]:
    """The Stage winners table: {stage label: {class: name}}. Sometimes filled in before the full results."""
    part = sections(text, 2).get("Stage winners") or ""
    found = tables(part)
    if not found or not found[0]:
        return {}
    rows = found[0]
    classes = [c["text"] for c in rows[0][1:]]
    out = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        label = stage_label(row[0]["text"])
        names = {cls: c["text"] for cls, c in zip(classes, row[1:]) if c["text"] and c["text"] not in ("-", "—", "–")}
        if names:
            out[label] = names
    return out


def parse_final(text: str) -> dict[str, list[dict]]:
    """Final standings per class (top ten): rank, name (co-driver for cars), make, time, gap."""
    part = sections(text, 2).get("Final standings") or ""
    out = {}
    for cls, body in sections(part, 3).items():
        found = tables(body)
        rows = []
        for row in found[0] if found else []:
            if not re.fullmatch(r"\d+", row[0]["text"]):
                continue
            cells = row[1:]
            co = len(cells) >= 5   # Rank, Driver, Co-driver, Car, Time, Difference
            rows.append({"pos": int(row[0]["text"]), "name": cells[0]["text"], "country": cells[0]["country"],
                         "co": cells[1]["text"] if co else "", "make": cells[2 if co else 1]["text"],
                         "time": cells[3 if co else 2]["text"] if len(cells) > (3 if co else 2) else "",
                         "gap": (cells[4 if co else 3]["text"] if len(cells) > (4 if co else 3) else "").lstrip("+")})
        if rows:
            out[cls] = rows
    return out


def parse(text: str) -> dict:
    results, winners = parse_results(text), parse_winners(text)
    classes = list(results) or sorted({c for w in winners.values() for c in w})
    return {"stages": parse_stages(text), "classes": classes, "results": results, "winners": winners,
            "final": parse_final(text)}


# ---- fetching -------------------------------------------------------------------------------------
async def _revid(year: int) -> int | None:
    q = (await get_json(f"{WIKI}?action=query&prop=revisions&rvprop=ids&format=json&formatversion=2&titles="
                        + quote(title(year)), HEADERS)).get("query") or {}
    page = (q.get("pages") or [{}])[0]
    return None if page.get("missing") else (page.get("revisions") or [{}])[0].get("revid")


async def refresh(store, year: int) -> dict | None:
    """Reads the year's article when it changed since the saved copy. Returns the saved data (new or old)."""
    hit = store.cached(key(year))
    old = hit[0] if hit else None
    rev = await _revid(year)
    if not rev:
        return old
    if old and old.get("revid") == rev:
        return old
    p = (await get_json(f"{WIKI}?action=parse&prop=wikitext|revid&format=json&formatversion=2&page="
                        + quote(title(year)), HEADERS)).get("parse") or {}
    data = {"year": year, "revid": p.get("revid") or rev, "url": article_url(year), **parse(p.get("wikitext") or "")}
    if old and not data["stages"] and old.get("stages"):
        log.warning("dakar %s: revision %s has no stages table, keeping %s", year, rev, old.get("revid"))
        return old
    store.save_cached(key(year), data)
    return data


def event(store, today: date | None = None) -> dict | None:
    """This Dakar (the w2rc event named Dakar that hasn't been over for a fortnight), else the most recent one."""
    today = today or datetime.now(timezone.utc).date()
    rows = [e for e in store.events(SERIES) if "dakar" in (e.get("name") or "").lower() and e.get("start_day")]
    rows.sort(key=lambda e: e["start_day"])
    cut = (today - timedelta(days=14)).isoformat()
    return next((e for e in rows if (e.get("end_day") or e["start_day"]) >= cut), rows[-1] if rows else None)


def data(store, year: int) -> dict | None:
    hit = store.cached(key(year))
    return hit[0] if hit else None


def _close(day: str) -> str:
    return f"{day}T{CLOSE_UTC}:00Z"


def winner(d: dict, stage: str, cls: str) -> str | None:
    rows = ((d.get("results") or {}).get(cls) or {}).get(stage, {}).get("stage") or []
    top = next((r for r in rows if r["pos"] == 1), None)
    return top["name"] if top else (d.get("winners") or {}).get(stage, {}).get(cls)


def rally_day(now: datetime) -> str:
    """The day in Morocco (UTC+1), the clock stage days are on, for Home and the event page alike."""
    return (now + timedelta(hours=1)).date().isoformat()


def summary(store, now: datetime | None = None) -> dict | None:
    """What Home and the event page show: the stage list (each with its date, route, km and Bikes/Cars winners),
    today's stage, the latest stage with results and per class its top three plus the overall top three, and the
    final top ten once posted."""
    now = now or datetime.now(timezone.utc)
    ev = event(store, now.date())
    if not ev:
        return None
    year = int(ev["start_day"][:4])
    d = data(store, year)
    out = {"year": year, "event_key": ev["key"], "start_day": ev["start_day"], "end_day": ev.get("end_day") or ev["start_day"],
           "day": rally_day(now),
           "url": article_url(year), "stages": [], "classes": [], "today": None, "latest": None, "results": {}, "final": {},
           "picks": open_window(store, now.date())}
    if not d:
        return out
    today = rally_day(now)
    for s in d["stages"]:
        out["stages"].append({**s, "winners": {c: w for c in PICK_CLASSES if (w := winner(d, s["label"], c))}})
    out["today"] = next((s for s in out["stages"] if s["day"] == today), None)
    done = [s for s in out["stages"] if s["kind"] == "stage" and any(
        (d["results"].get(c) or {}).get(s["label"]) for c in d["classes"])]
    out["latest"] = done[-1]["label"] if done else None
    out["classes"] = [c for c in d["classes"] if d["results"].get(c)] or d["classes"]
    for c in out["classes"]:
        per = d["results"].get(c) or {}
        last = next((s["label"] for s in reversed(done) if per.get(s["label"])), None)
        if last:
            overall = next((per[s["label"]]["overall"] for s in reversed(done) if per.get(s["label"], {}).get("overall")), [])
            out["results"][c] = {"stage": last, "top": per[last]["stage"][:3], "overall": overall[:3]}
    out["final"] = d.get("final") or {}
    return out


# ---- alerts ---------------------------------------------------------------------------------------
def news(d: dict, store, today: date) -> list[dict]:
    """Stages with a Bikes or Cars winner not alerted yet. A winner counts once the same name has been read twice
    (one POLL apart), so a vandal's edit reverted within minutes never reaches anyone's phone. Each item carries
    every class with a winner so far (a later push for the same stage replaces the earlier notification, so it
    repeats what that one said) and the `keys` to mark once it's sent. Stages more than a day old are marked here,
    not returned (a late edit to stage 3 on day 9 isn't news)."""
    out, stale, seen = [], [], []
    for s in d["stages"]:
        if s["kind"] != "stage":
            continue
        won, keys = [], []
        for c in ALERT_CLASSES:
            name = winner(d, s["label"], c)
            if not name:
                continue
            k, twice = f"dakar:{d['year']}:{s['label']}:{c}", f"dakar-seen:{d['year']}:{s['label']}:{c}:{normalise(name)}"
            if not store.was_sent(twice):
                seen.append(twice)
                continue
            won.append(c)
            if not store.was_sent(k):
                keys.append(k)
        if not keys:
            continue
        if s["day"] >= (today - timedelta(days=1)).isoformat():
            out.append({"stage": s, "classes": won, "keys": keys})
        else:
            stale += keys
    store.mark_sent(stale + seen)
    return out


PARTICLES = {"de", "da", "del", "della", "di", "du", "van", "von", "der", "den", "le", "la", "dos", "das"}


def surname(name: str) -> str:
    """'Guillaume De Mévius' -> 'De Mévius': the last word and any particles before it (as web/app.js surname)."""
    w = name.split()
    i = len(w) - 1
    while i > 1 and w[i - 1].lower() in PARTICLES:
        i -= 1
    return " ".join(w[i:])


def alert_text(d: dict, item: dict, follows: set[str], mine: dict[str, str]) -> dict:
    """Title and body for one stage: winners, overall leaders, your followed names in the top three, your picks."""
    s, lines, yours = item["stage"], [], {}
    for c in item["classes"]:
        name = winner(d, s["label"], c)
        res = ((d["results"].get(c) or {}).get(s["label"])) or {"stage": [], "overall": []}
        make = next((r["make"] for r in res["stage"] if r["pos"] == 1), "")
        line = f"{c}: {name}{f' ({make})' if make else ''}"
        lead = res["overall"][:1]
        if lead and normalise(lead[0]["name"]) != normalise(name):
            line += f". {surname(lead[0]['name'])} leads overall"
        if mine.get(c):
            line += f" · your pick {surname(mine[c])} {'+1' if normalise(mine[c]) == normalise(name) else 'missed'}"
        lines.append(line)
        for where, rows in (("on the stage", res["stage"]), ("overall", res["overall"])):
            for r in rows:
                if matches(follows, [r["name"]]):
                    yours.setdefault(r["name"], []).append(f"P{r['pos']} {where}")
    body = "\n".join(lines) + ("\nYour drivers: " + "; ".join(f"{n} {', '.join(v)}" for n, v in yours.items()) if yours else "")
    return {"title": f"Dakar · {s['label']}", "body": body}


async def alert(store, alerter, d: dict, item: dict) -> None:
    for p in alerter.people():
        if SERIES not in p["enabled"] or not p["prefs"]["results"]:
            continue
        mine = picks_of(store, p["email"], d["year"], item["stage"]["label"])
        msg = alert_text(d, item, p["follows"], mine)
        await alerter.send(p["subs"], {**msg, "url": f"{alerter.base}/#/event/{SERIES}/{d.get('event_key') or ''}",
                                       "tag": f"dakar:{d['year']}:{item['stage']['label']}"})


# ---- the loop -------------------------------------------------------------------------------------
def _when(ev: dict | None, today: date) -> str:
    """'live' while the rally runs (and a few days after), 'soon' in the weeks before it, else 'idle'."""
    if not ev:
        return "idle"
    start, end = date.fromisoformat(ev["start_day"]), date.fromisoformat(ev.get("end_day") or ev["start_day"])
    if start - timedelta(days=1) <= today <= end + timedelta(days=AFTER_DAYS):
        return "live"
    return "soon" if start - timedelta(days=BEFORE_DAYS) <= today < start else "idle"


async def loop(store, alerter) -> None:
    last_soon: date | None = None
    while True:
        try:
            today = datetime.now(timezone.utc).date()
            ev = event(store, today)
            when = _when(ev, today)
            if ev and when != "idle":
                year = int(ev["start_day"][:4])
                if not data(store, year - 1):
                    await refresh(store, year - 1)   # last year's final top ten: the Pick'em field before stage 1
                if when == "live" or last_soon != today:
                    first = data(store, year) is None
                    d = await refresh(store, year)
                    last_soon = today
                    if d:
                        d = {**d, "event_key": ev["key"]}
                        for item in news(d, store, today):
                            store.mark_sent(item["keys"])   # before sending: a push that half fails isn't sent twice
                            if first:
                                continue                    # the first copy ever read isn't news
                            try:
                                await alert(store, alerter, d, item)
                            except Exception:
                                log.exception("dakar alert error (%s)", item["stage"]["label"])
        except Exception:
            log.exception("dakar loop error")
        await asyncio.sleep(POLL)


# ---- Pick'em: each stage's Bikes and Cars winner ----------------------------------------------------
def picks_of(store, email: str, year: int, stage: str) -> dict[str, str]:
    return {r["class"]: r["name"] for r in store.conn.execute(
        "SELECT class, name FROM dakar_pick WHERE email=? AND year=? AND stage=?", (email, year, stage))}


def open_window(store, today: date | None = None) -> bool:
    today = today or datetime.now(timezone.utc).date()
    ev = event(store, today)
    if not ev:
        return False
    start, end = date.fromisoformat(ev["start_day"]), date.fromisoformat(ev.get("end_day") or ev["start_day"])
    return start - timedelta(days=PICKS_FROM_DAYS) <= today <= end + timedelta(days=PICKS_UNTIL_DAYS)


def field(store, year: int, cls: str) -> list[dict]:
    """Who can be picked: this year's overall order, everyone in a stage top three, then last year's top ten and
    last year's stage top threes (up to FIELD names)."""
    seen, out = set(), []

    def add(r: dict) -> None:
        k = normalise(r["name"])
        if k and k not in seen and len(out) < FIELD:
            seen.add(k)
            out.append({"name": r["name"], "make": r.get("make") or ""})

    d = data(store, year) or {}
    per = (d.get("results") or {}).get(cls) or {}
    for label in reversed(list(per)):
        for r in per[label]["overall"]:
            add(r)
    for label in reversed(list(per)):
        for r in per[label]["stage"]:
            add(r)
    for w in (d.get("winners") or {}).values():
        if w.get(cls):
            add({"name": w[cls]})
    prev = data(store, year - 1) or {}
    for r in (prev.get("final") or {}).get(cls) or []:
        add(r)
    for x in ((prev.get("results") or {}).get(cls) or {}).values():   # last year's fast ones, stage by stage
        for r in x["stage"]:
            add(r)
    return out


def _stages(store, year: int) -> list[dict]:
    return [s for s in (data(store, year) or {}).get("stages") or [] if s["kind"] == "stage"]


def choose(stages: list[dict], wanted: str | None, now: datetime) -> dict | None:
    if not stages:
        return None
    if wanted:
        hit = next((s for s in stages if s["label"].lower().replace(" ", "-") == wanted.lower().replace(" ", "-")), None)
        if hit:
            return hit
    stamp = _iso(now)
    return next((s for s in stages if _close(s["day"]) > stamp), stages[-1])


def set_pick(store, email: str, year: int, stage: dict, cls: str, name: str | None, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if cls not in PICK_CLASSES:
        raise ValueError("Unknown class.")
    if _close(stage["day"]) <= _iso(now):
        raise ValueError("Picks for this stage have closed.")
    with store.lock, store.conn:
        if not name:
            store.conn.execute("DELETE FROM dakar_pick WHERE email=? AND year=? AND stage=? AND class=?",
                               (email, year, stage["label"], cls))
            return
        if normalise(name) not in {normalise(f["name"]) for f in field(store, year, cls)}:
            raise ValueError("Pick someone from the list.")
        store.conn.execute("INSERT OR REPLACE INTO dakar_pick VALUES (?,?,?,?,?,?)",
                           (email, year, stage["label"], cls, name, _iso(now)))


def _points(d: dict, stage: str, cls: str, name: str) -> int | None:
    w = winner(d, stage, cls)
    return None if not w else int(normalise(w) == normalise(name))


def board(store, year: int, names: dict[str, str], me: str) -> list[dict]:
    """Points are worked out from the article on every read, so a corrected result corrects the table."""
    d = data(store, year) or {}
    rows: dict[str, dict] = {}
    for r in store.conn.execute("SELECT email, stage, class, name FROM dakar_pick WHERE year=?", (year,)):
        if r["email"] not in names:
            continue
        p = rows.setdefault(r["email"], {"name": names[r["email"]], "pts": 0, "picked": 0, "me": r["email"] == me})
        pts = _points(d, r["stage"], r["class"], r["name"])
        if pts is not None:
            p["pts"] += pts
            p["picked"] += 1
    return sorted((p for p in rows.values() if p["picked"]), key=lambda p: (-p["pts"], p["picked"], p["name"]))


def view(store, me: str, names: dict[str, str], wanted: str | None = None, now: datetime | None = None) -> dict:
    """Everything the Dakar tab of Pick'em shows."""
    now = now or datetime.now(timezone.utc)
    ev = event(store, now.date())
    year = int(ev["start_day"][:4]) if ev else now.year
    stages = _stages(store, year)
    s = choose(stages, wanted, now)
    out = {"year": year, "url": article_url(year), "event_key": ev["key"] if ev else None,
           "start_day": ev["start_day"] if ev else None, "stage": None, "prev": None, "next": None, "classes": [],
           "board": board(store, year, names, me)}
    if not s:
        return out
    i = stages.index(s)
    out["prev"] = stages[i - 1]["label"] if i else None
    out["next"] = stages[i + 1]["label"] if i + 1 < len(stages) else None
    closed = _close(s["day"]) <= _iso(now)
    out["stage"] = {**s, "closes": _close(s["day"]), "closed": closed}
    d = data(store, year) or {}
    everyone = [dict(r) for r in store.conn.execute(
        "SELECT email, class, name FROM dakar_pick WHERE year=? AND stage=?", (year, s["label"])) if r["email"] in names]
    for c in PICK_CLASSES:
        mine = next((r["name"] for r in everyone if r["email"] == me and r["class"] == c), None)
        others = [r for r in everyone if r["class"] == c and r["email"] != me]
        out["classes"].append({
            "class": c, "field": field(store, year, c), "mine": mine, "winner": winner(d, s["label"], c),
            "points": _points(d, s["label"], c, mine) if mine else None, "count": len(others),
            "everyone": [{"name": names[r["email"]], "pick": r["name"]} for r in others] if closed else []})
    return out
