"""Formula DRIFT (PRO) from formulad.com (Next.js; its events API is closed, so the public pages are read).

  /schedule                         the season's cards, each linking /schedule/<year>/<slug>
  /schedule/<year>/<slug>           title ("SHORELINE SHOWDOWN"), <time dateTime> + "OCT 23 - OCT 24", "LONG BEACH,
                                    CALIFORNIA, USA", the track, and a weekend schedule in local time with no zone:
                                    "Friday, October 23, 2026" / "11:00AM - 12:30PM" / "PRACTICE" ... Livestreamed
                                    sessions read "LIVESTREAM: <url> (QUALIFYING)". Box office, autographs etc. are skipped.
  /standings/<year>/pro             a plain table: Rank, "NAME/#number", rounds..., Total
Every round is in the US, so the state names the time zone.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import PRACTICE, QUALIFYING, RACE, AdapterResult, Entry, Event, Session, StandingRow, Standings
from .base import get_text, html_tables, slugify

SITE = "https://www.formulad.com"
STATE_TZ = {
    "california": "America/Los_Angeles", "nevada": "America/Los_Angeles", "washington": "America/Los_Angeles",
    "oregon": "America/Los_Angeles", "arizona": "America/Phoenix", "utah": "America/Denver", "colorado": "America/Denver",
    "texas": "America/Chicago", "illinois": "America/Chicago", "wisconsin": "America/Chicago", "missouri": "America/Chicago",
    "tennessee": "America/Chicago", "alabama": "America/Chicago",
}  # everything else FD visits (Georgia, Florida, Connecticut, Indiana, New Jersey...) is Eastern
EASTERN = "America/New_York"
# Track sessions worth a calendar entry: (pattern, name, kind). Box office, autographs, track walks etc. are not.
# Warm-ups (three or four a weekend) are left out too: noise on a schedule.
SESSIONS = [(r"top 16", "Top 16", RACE), (r"top 32", "Top 32", RACE), (r"qualif", "Qualifying", QUALIFYING),
            (r"practice", "Practice", PRACTICE)]
DAY = re.compile(r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [A-Z][a-z]+ \d{1,2}, \d{4}$")
CLOCK = re.compile(r"^(\d{1,2}:\d{2}\s*[AP]M)(?:\s*-\s*(\d{1,2}:\d{2}\s*[AP]M))?$", re.I)


def payload(page: str) -> str:
    """The page's React Server Components data, unescaped."""
    raw = "".join(re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', page, re.S))
    return raw.encode().decode("unicode_escape", errors="ignore").encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")


def strings(rsc: str) -> list[str]:
    return [html.unescape(s) for s in re.findall(r'"children":"([^"$]{1,120})"', rsc)]


def event_paths(page: str, year: int) -> list[str]:
    return list(dict.fromkeys(re.findall(rf'"href":"(/schedule/{year}/[^"]+)"', payload(page))))


def _title(text: str) -> str:
    return " ".join(w.capitalize() if len(w) > 2 or not w.isalpha() else w.lower() for w in text.split())


def parse_event(page: str, path: str) -> Event | None:
    rsc = payload(page)
    when = re.search(r'"dateTime":"(\d{4}-\d{2}-\d{2})","children":"([A-Z]{3} \d{2})(?: - ([A-Z]{3} \d{2}))?"', rsc)
    if not when:
        return None
    start = date.fromisoformat(when.group(1))
    end = datetime.strptime(f"{when.group(3)} {start.year}", "%b %d %Y").date() if when.group(3) else start
    words = strings(rsc)
    title = next((w.split(" | ")[0] for w in words if w.endswith("| Formula DRIFT")), path.rsplit("/", 1)[-1])
    location = next((w for w in words if re.match(r"^[A-Za-z .'-]+, [A-Za-z .'-]+, USA$", w)), "")
    track = words[words.index("Track") + 1] if "Track" in words and words.index("Track") + 1 < len(words) else ""
    city, state = (location.split(", ") + ["", ""])[:2]
    tz = ZoneInfo(STATE_TZ.get(state.lower(), EASTERN))
    sessions = parse_schedule(words, tz)
    if not sessions:
        sessions = [Session(key="weekend", name="Event weekend", kind=RACE, day=start, end_day=end)]
    return Event(key=f"{start.year}-{path.rsplit('/', 1)[-1]}", season=start.year, name=f"{_title(city)}: {_title(title)}",
                 venue=track or _title(city), country="USA", start_day=start, end_day=end, url=SITE + path, sessions=sessions)


def parse_schedule(words: list[str], tz: ZoneInfo) -> list[Session]:
    if "Schedule" not in words:
        return []
    sessions, day, clock = [], None, None
    for w in words[words.index("Schedule") + 1:]:
        w = w.strip()
        if w.startswith("Prohibited") or w.startswith("404"):
            break
        if DAY.match(w):
            day = datetime.strptime(w, "%A, %B %d, %Y").date()
            continue
        m = CLOCK.match(w)
        if m:
            clock = m.groups()
            continue
        if not (day and clock) or w.upper().startswith("LIVESTREAM") or w.startswith("http"):
            continue  # a livestreamed session's name follows its link, in "(...)"
        hit = next(((n, k) for p, n, k in SESSIONS if re.search(p, w, re.I)), None)
        if not hit or "walk" in w.lower():
            clock = None
            continue
        name, kind = hit
        start = datetime.strptime(f"{day} {clock[0].replace(' ', '')}", "%Y-%m-%d %I:%M%p").replace(tzinfo=tz)
        end = datetime.strptime(f"{day} {clock[1].replace(' ', '')}", "%Y-%m-%d %I:%M%p").replace(tzinfo=tz) if clock[1] else None
        if end and end <= start:
            end += timedelta(days=1)
        if end and end - start > timedelta(hours=8):
            end = None  # a typo on the page (AM for PM); the default length is closer than a 13-hour practice
        sessions.append(Session(key="", name=name, kind=kind, start=start, end=end))
        clock = None
    # Double-header weekends run the Top 32 and Top 16 twice: say which day. Two practices are numbered.
    count = {n: sum(1 for x in sessions if x.name == n) for n in {x.name for x in sessions}}
    numbered: dict[str, int] = {}
    for x in sessions:
        if count[x.name] > 1:
            if x.name == "Practice":
                numbered[x.name] = numbered.get(x.name, 0) + 1
                x.name = f"Practice {numbered[x.name]}"
            else:
                x.name = f"{x.name} ({x.start.strftime('%a')})"
        x.key = slugify(f"{x.start.date()} {x.name}")
    return sessions


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    schedule = await get_text(f"{SITE}/schedule")
    events = []
    for year in seasons:
        for path in event_paths(schedule, year):
            try:
                ev = parse_event(await get_text(SITE + path), path)
            except Exception:
                continue
            if ev:
                events.append(ev)
    entries = []
    try:
        entries = [Entry(r["name"], "", r["number"]) for r in parse_table(await get_text(f"{SITE}/standings/{seasons[0]}/pro"))]
    except Exception:
        pass
    return AdapterResult(events, sorted({e.season for e in events}), entries)


def parse_table(page: str) -> list[dict]:
    """Rows of the standings table: pos, name ("CONOR SHANAHAN/#79" -> "Conor Shanahan"), number, points."""
    table = next((t for t in html_tables(page) if t and t[0][:2] == ["Rank", "Driver"] and "Total" in t[0]), [])
    rows = []
    for r in table[1:]:
        if not r or not r[0].isdigit():
            continue
        name, _, number = r[1].partition("/#")
        try:
            rows.append({"pos": int(r[0]), "name": _title(name.strip()), "number": number.strip(),
                         "points": float(r[table[0].index("Total")] or 0)})
        except ValueError:
            continue
    return rows


async def standings(series_id: str) -> Standings | None:
    year = datetime.now().year
    rows = [StandingRow(r["pos"], r["name"], f"#{r['number']}" if r["number"] else "", r["points"])
            for r in parse_table(await get_text(f"{SITE}/standings/{year}/pro"))]
    return Standings(drivers=rows, season_label=f"{year} Formula DRIFT PRO") if rows else None
