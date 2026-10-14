"""GT World Challenge Europe (SRO) from gt-world-challenge-europe.com: calendar, timetables and entry lists.

Drivers move in and out of this series round by round (Verstappen's own team entered the 2026 Portimão
Endurance round), so every round's published entry list is read: the follow search finds a name there
just as it does on the WRC and WEC lists.

Pages:
  /calendar                     one card per round: dates, track, country, "Round N", Endurance or Sprint Cup
  /event/<id>/<slug>            timetable: a table per day, session / local time / GMT
  /entry-list/<year>/<slug>     car #, team, drivers 1-3, car, class
  /standings?filter_standing_type=<cup>_<class>_<drivers|teams>   cup 0 overall, 42 Endurance, 43 Sprint;
                                class 0 overall, 81 Gold, 82 Silver, 80 Bronze. Each driver of a crew has a row.
  /results                      the season's rounds, oldest first; /results/<year>/<round>/<session> = a classification
                                (Pos, Car #, Class, Drivers, Team, Car, Time, Laps, Gap). Races: race-1/race-2 (Sprint),
                                main-race (Endurance, Spa's final classification)
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

from ..models import RACE, AdapterResult, Entry, Event, ResultRow, Session, StandingRow, Standings, kind_from_name
from .base import get_text, slugify, tidy_caps

BASE = "https://www.gt-world-challenge-europe.com"
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def _person(name: str) -> str:
    """'Max VERSTAPPEN' -> 'Max Verstappen'; keeps 'van der', 'de' etc. as written."""
    return " ".join(w if not w.isupper() or len(w) <= 1 else w.capitalize() for w in _text(name).split())


def race_length(name: str, cup: str) -> timedelta:
    if "24 Hours" in name or "24 Hours" in cup:
        return timedelta(hours=24)
    if "Endurance" in cup:
        return timedelta(hours=3)  # the standard Endurance Cup race; Spa is the 24-hour one
    return timedelta(hours=1)      # Sprint Cup races


# ---- calendar ---------------------------------------------------------------------------------
def parse_calendar(page: str) -> list[dict]:
    """Rounds as dicts: name, country, cup, round, start/end dates, event page path. Test days are skipped."""
    out = []
    for card in re.split(r'(?=<div class="calendar__date-start">)', page)[1:]:
        nums = re.findall(r'calendar__date-number">\s*(\d+)', card)
        months = re.findall(r'calendar__date-month">\s*([A-Z]{3})', card)
        years = re.findall(r'calendar__date-year">\s*(\d{4})', card)
        name = re.search(r'calendar__race-header">(.*?)</h3>', card, re.S)
        link = re.search(r'href="(/event/(\d+)/[^"]+)"', card)
        texts = [_text(t) for t in re.findall(r'calendar__race-text">(.*?)</span>', card, re.S)]
        rnd = next((t for t in texts if t.startswith("Round")), "")
        cup = next((t for t in texts if "Cup" in t), "")
        if not (nums and months and years and name and link and rnd):
            continue  # official test days have no round number
        start = date(int(years[0]), MONTHS[months[0]], int(nums[0]))
        end = date(int(years[-1]), MONTHS[months[-1]], int(nums[-1]))
        country = re.search(r'calendar__race-subheading-text">(.*?)</span>', card, re.S)
        out.append({"name": _text(name.group(1)), "country": _text(country.group(1)) if country else "",
                    "cup": cup.replace("GT World Challenge Europe - ", ""), "round": rnd,
                    "start": start, "end": end, "path": html.unescape(link.group(1)), "id": link.group(2)})
    return out


# "19 - 20 May 2026", and across months "30 July 2026 - 2 August 2026" (the year then appears twice).
PAST_DATES = re.compile(r"\b(\d{1,2})(?: ([A-Za-z]+))?(?: \d{4})? - (\d{1,2}) ([A-Za-z]+) (\d{4})\b")


def parse_past(page: str) -> list[dict]:
    """Finished rounds: a simpler card (dates as text, track, country, round; no Cup name)."""
    out = []
    for item in re.findall(r'<li class="past-events__list-item">(.*?)</li>\s*</ul>.*?href="(/event/(\d+)/[^"]+)"', page, re.S):
        body, path, eid = item
        # The card's last field ("Round N") ends where the match above stopped, so allow end-of-text too.
        spans = [_text(x) for x in re.findall(r'past-events__piped-list-span">(.*?)</span>\s*(?:</li>|$)', body, re.S)]
        dates = next((PAST_DATES.search(x) for x in spans if PAST_DATES.search(x)), None)
        rnd = next((x for x in spans if x.startswith("Round")), "")
        strong = re.search(r"<strong>(.*?)</strong>\s*([^<]*)", body, re.S)
        if not (dates and rnd and strong):
            continue  # test days
        d1, m1, d2, m2, year = dates.groups()
        start = datetime.strptime(f"{d1} {(m1 or m2)[:3]} {year}", "%d %b %Y").date()
        end = datetime.strptime(f"{d2} {m2[:3]} {year}", "%d %b %Y").date()
        out.append({"name": _text(strong.group(1)), "country": _text(strong.group(2)), "cup": "", "round": rnd,
                    "start": start, "end": end, "path": html.unescape(path), "id": eid})
    return out


def cup_from_event(page: str) -> str:
    for cup in ("Endurance Cup", "Sprint Cup"):
        if f"GT World Challenge Europe - {cup}" in page or f"powered by AWS {cup}" in page:
            return cup
    return ""


# ---- one event: timetable and entry list -----------------------------------------------------------
def parse_timetable(page: str, year: int, cup: str, event_name: str) -> list[Session]:
    sessions = []
    for table in re.findall(r'<table class="timetable__table">(.*?)</table>', page, re.S):
        caption = _text((re.search(r"<caption[^>]*>(.*?)</caption>", table, re.S) or [None, ""])[1])
        m = re.search(r"(\d{1,2}) ([A-Za-z]+)", caption)
        if not m:
            continue
        day = datetime.strptime(f"{m.group(1)} {m.group(2)[:3]} {year}", "%d %b %Y").date()
        for row in re.findall(r"<tr>(.*?)</tr>", table, re.S):
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 3 or not re.match(r"\d{1,2}:\d{2}$", cells[2]):
                continue
            name, local, gmt = cells[0], cells[1], cells[2]
            if "Bronze" in name:
                continue  # a test only for bronze-rated (amateur) drivers: noise on everyone's schedule
            # GMT is UTC; the local time tells us whether GMT fell on the day before or after.
            lh, lm = map(int, local.split(":")) if re.match(r"\d{1,2}:\d{2}$", local) else (None, None)
            gh, gm = map(int, gmt.split(":"))
            start = datetime(day.year, day.month, day.day, gh, gm, tzinfo=timezone.utc)
            if lh is not None:
                diff = (lh * 60 + lm) - (gh * 60 + gm)
                if diff < -12 * 60:
                    start += timedelta(days=-1)  # local just after midnight, GMT still the evening before
                elif diff > 14 * 60:
                    start += timedelta(days=1)
            kind = kind_from_name(name)
            end = start + race_length(event_name, cup) if kind == RACE else None
            sessions.append(Session(key=slugify(f"{day.isoformat()} {name}"), name=name, kind=kind,
                                    start=start, end=end, day=day))
    return sessions


def parse_entry_list(page: str) -> list[dict]:
    """Rows of the entry table: number, team, drivers, car, class."""
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 5 or not _text(cells[0]).isdigit():
            continue
        drivers = [_person(c) for c in cells[2:-2] if _text(c) and _text(c).upper() != "TBA"]
        out.append({"number": _text(cells[0]), "team": tidy_caps(_text(cells[1])), "drivers": drivers,
                    "car": _text(cells[-2]), "class": _text(cells[-1]).title()})
    return out


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    page = await get_text(f"{BASE}/calendar")
    rounds = parse_past(page) + parse_calendar(page)
    events, entries, seen = [], [], set()
    for rd in rounds:
        season = rd["start"].year
        slug = rd["path"].rsplit("/", 1)[-1]
        sessions, entrants = [], None
        try:
            event_page = await get_text(BASE + rd["path"])
            rd["cup"] = rd["cup"] or cup_from_event(event_page)
            sessions = parse_timetable(event_page, season, rd["cup"], rd["name"])
        except Exception:
            pass  # the timetable isn't up yet: the round still shows, with its dates
        if not sessions:
            sessions = [Session(key="weekend", name=f"{rd['cup']} weekend", kind=RACE, day=rd["start"], end_day=rd["end"])]
        try:
            rows = parse_entry_list(await get_text(f"{BASE}/entry-list/{season}/{slug}"))
            entrants = [d for r in rows for d in r["drivers"]] or None
            for r in rows:
                for d in r["drivers"]:
                    if d not in seen:
                        seen.add(d)
                        entries.append(Entry(name=d, team=r["team"], number=r["number"]))
        except Exception:
            pass  # entry list not published yet
        name = f"{rd['name']} ({rd['cup'].replace(' Cup', '')})" if rd["cup"] else rd["name"]
        events.append(Event(key=f"{season}-{slugify(rd['name'])}", season=season, name=name, country=rd["country"],
                            venue=rd["name"], start_day=rd["start"], end_day=rd["end"],
                            url=BASE + rd["path"], sessions=sessions, entrants=entrants))
    return AdapterResult(events, sorted({e.season for e in events}), entries)


# ---- standings and results ----------------------------------------------------------------------
# (filter value, label) for the class picker on the Drivers tab; the overall championship is the main table.
CLASS_TABLES = [("42_0_drivers", "Endurance Cup"), ("43_0_drivers", "Sprint Cup"), ("0_81_drivers", "Gold Cup"),
                ("0_82_drivers", "Silver Cup"), ("0_80_drivers", "Bronze Cup")]
RACE_SESSION = re.compile(r"^(race-\d+|race|main-race)$")


def _num(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_standings(page: str, people: bool = True) -> list[tuple[int, str, float]]:
    """(position, name, points) per row of the standings table; header rows are skipped. Driver names get the
    'Max VERSTAPPEN' tidy-up; team names (people=False) are kept as written ('Team WRT')."""
    table = re.search(r"<table[^>]*standing.*?</table>", page, re.S)
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0) if table else "", re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 3 or not _text(cells[0]).isdigit():
            continue
        out.append((int(_text(cells[0])), _person(cells[1]) if people else _text(cells[1]), _num(_text(cells[2]))))
    return out


def crews(rows: list[tuple[int, str, float]], known: dict[str, tuple[str, str]] | None = None) -> list[StandingRow]:
    """Drivers who share a car share a position and points: one row per crew, 'A / B'. `known` maps a lower-case
    name to (name as the results write it, team): standings write 'VAN DER LINDE', results 'van der Linde'."""
    out: list[StandingRow] = []
    for pos, name, pts in rows:
        proper, team = (known or {}).get(name.lower(), (name, ""))
        if out and out[-1].pos == pos and out[-1].points == pts:
            out[-1].name += f" / {proper}"
            out[-1].team = out[-1].team or team
            continue
        out.append(StandingRow(pos, proper, team, pts))
    return out


def parse_results_index(page: str, year: int) -> list[str]:
    """The season's round pages in the order the site lists them (oldest first), test days left out."""
    seen, out = set(), []
    for path in re.findall(rf'href="(/results/{year}/[^"/#]+)"', page):
        path = html.unescape(path)
        if path not in seen and "test" not in path:
            seen.add(path)
            out.append(path)
    return out


def race_sessions(page: str, round_path: str) -> list[str]:
    """A round's race classifications (not practice, qualifying or the Spa hourly snapshots), in race order."""
    found = []
    for slug in re.findall(rf'href="{re.escape(round_path)}/([^"/#]+)"', page):
        if RACE_SESSION.match(slug) and slug not in found:
            found.append(slug)
    return sorted(found, key=lambda x: (x != "main-race", x))


def round_name(page: str) -> str:
    """The round's name as the site's round picker shows it ('Barcelona', 'CrowdStrike 24 Hours of Spa')."""
    picker = re.search(r'<select[^>]*name="filter_meeting_id".*?</select>', page, re.S)
    chosen = re.search(r"<option[^>]*selected[^>]*>(.*?)</option>", picker.group(0) if picker else "", re.S)
    return _text(chosen.group(1)) if chosen else ""


def parse_classification(page: str) -> list[dict]:
    table = re.search(r'<table[^>]*class="table session".*?</table>', page, re.S)
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0) if table else "", re.S):
        c = [_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(c) < 9:
            continue
        out.append({"pos": c[0], "car": c[1], "class": c[2], "drivers": [_person(d) for d in c[3].split(",") if d.strip()],
                    "team": c[4], "model": c[5], "time": c[6], "laps": c[7], "gap": c[8]})
    return out


def result_rows(cls: list[dict]) -> list[ResultRow]:
    rows = []
    for i, r in enumerate(cls):
        if not r["pos"].isdigit():
            when = r["pos"]                                     # NC, DNF, DSQ: say so, not a meaningless gap
        elif i == 0:
            when = f"{r['laps']} laps" if r["laps"] else ""
        elif r["gap"]:
            when = r["gap"] if not re.match(r"^[\d:.]+$", r["gap"]) else f"+{r['gap']}"
        else:
            when = r["pos"] if not r["pos"].isdigit() else ""
        team = " · ".join(x for x in (f"#{r['car']} {r['team']}".strip(), r["class"]) if x and x != "#")
        rows.append(ResultRow(r["pos"], " / ".join(r["drivers"]), team, when))
    return rows


async def standings(series_id: str) -> Standings | None:
    year = datetime.now().year
    index = await get_text(f"{BASE}/results")
    rounds = parse_results_index(index, year) or parse_results_index(index, year - 1)
    if not rounds:
        return None
    season = int(rounds[0].split("/")[2])
    # The latest round with a race classification gives "Last race". Teams for the standings come from the latest
    # Sprint and the latest Endurance race (crews differ between the two Cups).
    last_name, last_session, last_rows = "", "", []
    known: dict[str, tuple[str, str]] = {}   # lower-case driver name -> (name as written, team)
    seen_kinds: set[str] = set()
    for path in reversed(rounds):
        page = await get_text(BASE + path)
        races = race_sessions(page, path)
        if not races:
            continue
        kind = "endurance" if races[-1] == "main-race" else "sprint"
        if kind in seen_kinds:
            continue
        cls = parse_classification(await get_text(f"{BASE}{path}/{races[-1]}"))
        if not cls:
            continue
        seen_kinds.add(kind)
        for r in cls:
            for d in r["drivers"]:
                known.setdefault(d.lower(), (d, r["team"]))
        if not last_rows:
            last_name = round_name(page) or path.rsplit("/", 1)[-1]
            last_session = "Race" if races[-1] == "main-race" else races[-1].replace("-", " ").capitalize()
            last_rows = result_rows(cls)
        if len(seen_kinds) == 2:
            break
    drivers = crews(parse_standings(await get_text(f"{BASE}/standings?filter_standing_type=0_0_drivers")), known)
    teams = [StandingRow(p, n, "", pts) for p, n, pts in
             parse_standings(await get_text(f"{BASE}/standings?filter_standing_type=0_0_teams"), people=False)]
    st = Standings(drivers=drivers, teams=teams, season_label=str(season), last_event=last_name,
                   last_session=last_session, last_results=last_rows)
    for flt, label in CLASS_TABLES:
        try:
            rows = crews(parse_standings(await get_text(f"{BASE}/standings?filter_standing_type={flt}")), known)
        except Exception:
            continue  # one missing table never costs the rest
        if rows:
            st.classes[label] = rows
    return st
