"""NASCAR Cup, O'Reilly Auto Parts (formerly Xfinity) and Craftsman Truck Series.

All from the public JSON NASCAR's own site and app use (cf.nascar.com/cacher):
race_list_basic.json for every race with its weekend schedule (UTC) and TV network,
points-feed.json for standings, <race>/weekend-feed.json for results.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..models import PRACTICE, QUALIFYING, RACE, AdapterResult, Entry, Event, ResultRow, Session, StandingRow, Standings
from .base import get_json, slugify

CACHER = "https://cf.nascar.com/cacher"
MAKES = {"Chv": "Chevrolet", "Frd": "Ford", "Tyt": "Toyota"}  # the timing feed abbreviates them
SERIES_NO = {"cup": 1, "noaps": 2, "truck": 3}
RUN_KIND = {1: PRACTICE, 2: QUALIFYING, 3: RACE}  # schedule run_type; 0 = meetings, garage hours etc.
EASTERN = ZoneInfo("America/New_York")           # race_date in the feed is track-agnostic US Eastern
# Cup races routinely run 3-4 hours (cautions, overtime); the default 2 h would end live timing early.
LENGTH = {RACE: timedelta(hours=4), QUALIFYING: timedelta(hours=1, minutes=30), PRACTICE: timedelta(hours=1)}


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def parse_races(races: list[dict]) -> list[Event]:
    events = []
    for r in races:
        tv = (r.get("television_broadcaster") or "").strip()
        sessions = []
        for item in r.get("schedule") or []:
            kind = RUN_KIND.get(item.get("run_type"))
            if kind and item.get("start_time_utc"):
                name = item.get("event_name") or kind.title()
                start = _utc(item["start_time_utc"])
                sessions.append(Session(slugify(name) or kind, name, kind, start=start, end=start + LENGTH[kind],
                                        tv=tv if kind == RACE else ""))
        if not any(s.kind == RACE for s in sessions) and r.get("race_date", "").startswith(str(r.get("race_season"))):
            start = datetime.fromisoformat(r["race_date"]).replace(tzinfo=EASTERN)
            sessions.append(Session("race", "Race", RACE, start=start, end=start + LENGTH[RACE], tv=tv))
        if not sessions:
            continue
        events.append(Event(key=f"{r['race_season']}-{r['race_id']}", season=r["race_season"], name=r["race_name"],
                            venue=r.get("track_name", ""), country="USA",
                            url="https://www.nascar.com/schedule/", sessions=sessions))
    return events


async def fetch(series_id: str, seasons: list[int]) -> AdapterResult:
    events, covered = [], []
    for season in seasons:
        try:
            data = await get_json(f"{CACHER}/{season}/race_list_basic.json")
        except Exception:
            continue  # next season's file appears when NASCAR publishes the schedule
        season_events = parse_races(data.get(f"series_{SERIES_NO[series_id]}") or [])
        if season_events:
            events += season_events
            covered.append(season)
    try:
        entries = [Entry(_name(p["driver_name"]), p.get("manufacturer", ""), p.get("car_no", ""))
                   for p in await _points(series_id)]
    except Exception:
        entries = []
    return AdapterResult(events, covered, entries)


def _name(name: str) -> str:
    """The feeds tag names: "Chase Briscoe (C)" = in the Chase (playoffs), "(i)" = not points-eligible,
    a leading "*" = not running for the championship."""
    return name.replace("(C)", "").replace("(i)", "").replace("(P)", "").strip().lstrip("*").strip()


async def _points(series_id: str) -> list[dict]:
    year = datetime.now(timezone.utc).year
    for season in (year, year - 1):
        try:
            rows = await get_json(f"{CACHER}/{season}/{SERIES_NO[series_id]}/points-feed.json")
        except Exception:
            continue
        if rows:
            return rows
    return []


def _laps_down(n: int) -> str:
    return f"{n} lap{'s' if n != 1 else ''} down"


def parse_timing_results(feed: dict) -> list[ResultRow]:
    """Finishing order from a race's final live-timing feed (live/feeds/series_<n>/<race>/live_feed.json)."""
    cars = sorted((v for v in feed.get("vehicles") or [] if v.get("running_position")), key=lambda v: v["running_position"])
    laps = cars[0].get("laps_completed") or 0 if cars else 0
    out = []
    for v in cars:
        down = laps - (v.get("laps_completed") or 0)
        # delta = seconds behind the winner on the lead lap (negative = laps down)
        time = ("Winner" if v["running_position"] == 1 else
                f"Out (lap {v.get('laps_completed') or 0})" if v.get("status") not in (None, 1) else
                _laps_down(down) if down > 0 else f"+{max(v.get('delta') or 0, 0):.3f}")
        out.append(ResultRow(str(v["running_position"]), _name((v.get("driver") or {}).get("full_name", "")),
                             f"#{v.get('vehicle_number', '')} · {MAKES.get(v.get('vehicle_manufacturer', ''), v.get('vehicle_manufacturer', ''))}", time, 0))
    return out


async def _timing_results(series_id: str, race_id: int) -> list[ResultRow]:
    try:
        return parse_timing_results(await get_json(f"https://cf.nascar.com/live/feeds/series_{SERIES_NO[series_id]}/{race_id}/live_feed.json"))
    except Exception:
        return []


async def standings(series_id: str) -> Standings | None:
    points = await _points(series_id)
    if not points:
        return None
    st = Standings(season_label="Points (Chase standings while the Chase is on)")
    st.drivers = [StandingRow(p["position"], _name(p["driver_name"]), f"#{p.get('car_no', '')} · {p.get('manufacturer', '')}",
                              p.get("points") or 0, wins=p.get("wins")) for p in sorted(points, key=lambda p: p["position"])]
    # Last race: the most recent race that has results. The weekend feed's official results can take a day or two
    # to appear after a race; until then the race's final timing feed gives the order (no points or teams there).
    year = datetime.now(timezone.utc).year
    races = (await get_json(f"{CACHER}/{year}/race_list_basic.json")).get(f"series_{SERIES_NO[series_id]}") or []
    done = sorted((r for r in races if r.get("winner_driver_id")), key=lambda r: r["race_date"])
    if done:
        last = done[-1]
        feed = await get_json(f"{CACHER}/{year}/{SERIES_NO[series_id]}/{last['race_id']}/weekend-feed.json")
        race = (feed.get("weekend_race") or [{}])[0]
        results = sorted((x for x in race.get("results") or [] if x.get("finishing_position")),
                         key=lambda x: x["finishing_position"])
        if results:
            st.last_event, st.last_session = race.get("race_name", last["race_name"]), "Race"
            laps = results[0].get("laps_completed") or 0
            for x in results:
                down = laps - (x.get("laps_completed") or 0)
                status = x.get("finishing_status") or ""
                time = (last.get("total_race_time") or "Winner" if x["finishing_position"] == 1 else
                        f"{status} (lap {x.get('laps_completed') or 0})" if status and status != "Running" else
                        _laps_down(down) if down > 0 else f"+{(x.get('diff_time') or 0) / 1000:.3f}")
                st.last_results.append(ResultRow(str(x["finishing_position"]), _name(x.get("driver_fullname", "")),
                                                 f"#{x.get('car_number', '')} · {x.get('team_name', '')}", time,
                                                 x.get("points_earned") or 0))
        else:
            st.last_event, st.last_session, st.last_results = last["race_name"], "Race (unofficial)", \
                await _timing_results(series_id, last["race_id"])
            if st.last_results and last.get("total_race_time"):
                st.last_results[0].time = last["total_race_time"]
    return st


async def history(series_id: str, season: int, have: set[str]) -> list[dict]:
    """Official results of every finished race not stored yet: [{event, day, rows: {name: [pos, points]}}]."""
    races = (await get_json(f"{CACHER}/{season}/race_list_basic.json")).get(f"series_{SERIES_NO[series_id]}") or []
    out = []
    for last in sorted((r for r in races if r.get("winner_driver_id")), key=lambda r: r["race_date"]):
        if last["race_name"] in have:
            continue
        try:
            feed = await get_json(f"{CACHER}/{season}/{SERIES_NO[series_id]}/{last['race_id']}/weekend-feed.json")
        except Exception:
            continue
        race = (feed.get("weekend_race") or [{}])[0]
        rows = {_name(x.get("driver_fullname", "")): [str(x["finishing_position"]), x.get("points_earned") or 0]
                for x in race.get("results") or [] if x.get("finishing_position")}
        if rows:
            out.append({"event": race.get("race_name") or last["race_name"], "day": last["race_date"][:10], "rows": rows})
    return out
