"""Parsing and storage tests. Fixtures are trimmed copies of real source markup."""
from datetime import date, datetime, timedelta, timezone

from lightsout import ics
from lightsout.adapters import fia_feeder, manual, wrc
from lightsout.db import Store
from lightsout.models import PRACTICE, QUALIFYING, RACE, SPRINT, AdapterResult, Entry, Event, Session, kind_from_name


def test_kind_from_name():
    assert kind_from_name("Sprint Qualifying") == QUALIFYING
    assert kind_from_name("Sprint") == SPRINT
    assert kind_from_name("Hyperpole - LMGT3") == QUALIFYING
    assert kind_from_name("Free Practice 1") == PRACTICE
    assert kind_from_name("Feature Race") == RACE


def test_wrc_calendar_dates_and_month_wrap():
    page = """
    <a href="/en/events/wrc-rally-us-2027"><h3>WRC Rally US 2027</h3><p>26 – 29 August 2027</p><p>United States</p></a>
    <a href="/en/events/wrc-rally-us-2027">Rally US</a>
    <a href="/en/events/wrc-secto-rally-finland-2027"><h3>WRC Secto Rally Finland 2027</h3><p>29 July – 1 August 2027</p><p>Finland</p></a>
    """
    events = {e.key: e for e in wrc.parse_calendar(page, 2027)}
    us = events["2027-wrc-rally-us"]
    assert us.name == "Rally US" and us.country == "United States"
    assert [s.day for s in us.sessions] == [date(2027, 8, d) for d in (26, 27, 28, 29)]
    assert us.sessions[0].name == "Day 1 (Thu)"
    fin = events["2027-wrc-secto-rally-finland"]
    assert fin.sessions[0].day == date(2027, 7, 29) and fin.sessions[-1].day == date(2027, 8, 1)


def test_fia_feeder_placeholder_times_are_tba():
    page = ('"meetingSessions":[{"session":"Practice 0","startTime":"2026-12-04T00:00:00","endTime":"2026-12-04T01:00:00",'
            '"gmtOffset":"+04:00","sessionType":"Practice","sessionStatus":"TBC"},'
            '{"session":"Race 2","startTime":"2026-12-06T13:15:00","endTime":"2026-12-06T14:15:00",'
            '"gmtOffset":"+04:00","sessionType":"Race","sessionStatus":"Confirmed"}]')
    ev = fia_feeder.parse_round(page, 2026, "yas-marina", "u")
    practice, feature = ev.sessions
    assert practice.time_tba and practice.day == date(2026, 12, 4)
    assert feature.name == "Feature Race" and feature.kind == RACE
    assert feature.start.astimezone(timezone.utc).hour == 9


def test_manual_formats():
    rally = manual.build_event({"name": "Rally Colorado", "start": "2026-07-18", "end": "2026-07-19", "rally_days": True})
    assert [s.name for s in rally.sessions] == ["Day 1 (Sat)", "Day 2 (Sun)"]
    raid = manual.build_event({"name": "Dakar", "start": "2027-01-01", "end": "2027-01-15"})
    assert len(raid.sessions) == 1 and raid.sessions[0].end_day == date(2027, 1, 15)


def test_store_replaces_season_and_feeds_ics(tmp_path):
    store = Store(tmp_path / "t.db")
    start = datetime(2026, 10, 23, 17, 30, tzinfo=timezone.utc)
    ev = Event("2026-r20", 2026, "United States Grand Prix", "COTA", "USA",
               sessions=[Session("fp1", "Practice 1", PRACTICE, start=start)])
    store.save("f1", AdapterResult([ev], [2026]))
    # A second run for the same season replaces, not duplicates.
    ev2 = Event("2026-r20", 2026, "United States Grand Prix", "COTA", "USA",
                sessions=[Session("fp1", "Practice 1", PRACTICE, start=start)])
    store.save("f1", AdapterResult([ev2], [2026]))
    rows = store.sessions("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z")
    assert len(rows) == 1 and rows[0]["end_utc"] == "2026-10-23T18:30:00Z"

    raid = manual.build_event({"name": "Dakar Rally with a deliberately long name to force line folding",
                               "start": "2027-01-01", "end": "2027-01-15"})
    store.save("w2rc", AdapterResult([raid], [2027]))
    out = ics.build(store.sessions("2026-10-01T00:00:00Z", "2027-02-01T00:00:00Z"), "https://example.test")
    assert "DTSTART:20261023T173000Z" in out
    assert "DTSTART;VALUE=DATE:20270101" in out and "DTEND;VALUE=DATE:20270116" in out
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n"))


def test_driver_rosters():
    from lightsout.adapters import formulae
    f2 = fia_feeder.parse_drivers('<a href="/en/drivers/rafael-camara"><span>Rafael</span><span>Câmara</span></a>'
                                  '<a href="/en/drivers/rafael-camara">profile</a>')
    assert [e.name for e in f2] == ["Rafael Câmara"]
    fe = formulae.parse_drivers('<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"item":'
                                '{"@type":"Person","name":"Oliver Rowland","memberOf":{"name":"NISSAN FORMULA E TEAM"}}}]}</script>')
    assert (fe[0].name, fe[0].team) == ("Oliver Rowland", "NISSAN FORMULA E TEAM")


def test_ara_schedule_page():
    from lightsout.adapters import ara
    page = ("<h2>2027 ARA National Championship</h2><p>Sno*Drift Rally</p><p>(National/East)</p>"
            "<p>February 5-6, 2027</p><p>Atlanta, Michigan</p><p>Ridge Rally</p><p>(2027 West)</p>"
            "<p>December 4-5, 2026</p><p>Tehachapi, California</p>")
    events = ara.parse_schedule(page, 2027)
    assert [e.name for e in events] == ["Sno*Drift Rally"]  # regional rounds are skipped
    assert events[0].venue == "Atlanta, Michigan"
    assert [s.day for s in events[0].sessions] == [date(2027, 2, 5), date(2027, 2, 6)]


def test_erx_calendar_cards():
    from lightsout.adapters import erx
    page = ('<h2 class="widget-title">2027 CALENDAR</h2><ul><li class="more-items-item"><h2><a href="#">'
            '<span class="event-name">PartsForCars Euro RX Of<br><span class="city"> IRELAND</span></span></a></h2>'
            '<p class="more-items-text">Mondello Park</p><div class="more-items-long-text">30 May - 01 Jun</div></li></ul>')
    (ev,) = erx.parse_calendar(page)
    assert ev.key == "2027-euro-rx-of-ireland" and ev.venue == "Mondello Park"
    assert [s.day for s in ev.sessions] == [date(2027, 5, 30), date(2027, 5, 31), date(2027, 6, 1)]
    assert ev.sessions[-1].name == "Semi-finals and final"


def test_standings_tables():
    from lightsout.adapters import formulae, indycar
    fe = ('<table><tr><th>POS</th><th>DRIVER</th><th>TEAM</th><th>PTS</th></tr>'
          '<tr><td>1</td><th>Pascal Wehrlein<span>DEU</span>PORSCHE FORMULA E TEAM</th>'
          '<td>PORSCHE FORMULA E TEAM</td><td>169</td></tr></table>')
    (row,) = formulae.parse_driver_standings(fe)
    assert (row.pos, row.name, row.team, row.points) == (1, "Pascal Wehrlein", "Porsche Formula E Team", 169)
    indy = ('<table><tr><th>Rank</th><th>No.</th><th>Driver</th><th>Team</th><th>Points</th></tr>'
            '<tr><td>1</td><td></td><td>Alex Palou</td><td></td><td>631</td></tr></table>')
    (row,) = indycar.parse_standings(indy, {"Alex Palou": "Chip Ganassi Racing"})
    assert (row.name, row.team, row.points) == ("Alex Palou", "Chip Ganassi Racing", 631)


def test_f1_live_merge_and_snapshot():
    from lightsout.live.f1 import F1State, merge
    assert merge([{"a": 1}], {"0": {"b": 2}, "1": {"c": 3}}) == [{"a": 1, "b": 2}, {"c": 3}]
    st = F1State()
    st.apply("SessionInfo", {"Name": "Race", "Type": "Race", "Meeting": {"Name": "US Grand Prix"}})
    st.apply("DriverList", {"1": {"Tla": "NOR", "FullName": "Lando NORRIS", "TeamName": "McLaren", "TeamColour": "F47600"}})
    st.apply("TimingData", {"Lines": {"1": {"Position": "2", "GapToLeader": "+1.2",
                                            "IntervalToPositionAhead": {"Value": "+1.2"}}}})
    st.apply("TimingData", {"Lines": {"1": {"Position": "1", "GapToLeader": "LAP 3"}}})
    st.apply("TimingAppData", {"Lines": {"1": {"Stints": [{"Compound": "MEDIUM"}]}}})
    st.apply("TimingAppData", {"Lines": {"1": {"Stints": {"1": {"Compound": "HARD"}}}}})
    snap = st.snapshot()
    (row,) = snap["rows"]
    assert snap["kind"] == "race" and snap["event"] == "US Grand Prix"
    assert (row["pos"], row["code"], row["name"], row["gap"], row["tyre"]) == (1, "NOR", "Lando Norris", "LAP 3", "H")


def test_wrc_and_feeder_helpers():
    from lightsout.adapters.base import tidy_caps
    from lightsout.adapters.wrc import ms_to_time
    assert ms_to_time(10275800) == "2:51:15.8" and ms_to_time(20700, plus=True) == "+20.7"
    assert tidy_caps("TOYOTA GAZOO RACING WRT2") == "Toyota Gazoo Racing WRT2"
    assert tidy_caps("None") == "" and tidy_caps("Lancia Corse HF") == "Lancia Corse HF"
    page = ('<table><tr><td>Driver</td><td>FR</td><td>Points</td></tr>'
            '<tr><td>1R. Câmara</td><td>18</td><td>207</td></tr></table>')
    (row,) = fia_feeder.parse_standings(page, {"r. camara": "Rafael Câmara"})
    assert (row.pos, row.name, row.points) == (1, "Rafael Câmara", 207)


def test_fia_live_lines_merge():
    from lightsout.live.fia import FiaState
    st = FiaState("f2")
    st.load({"data": ["t", {"Session": "Race"}, [{"driver": {"RacingNumber": "1", "TLA": "CAM", "FullName": "Rafael CAMARA"},
                                                   "position": {"Value": "2"}, "gap": {"Value": "+0.7"}}]],
             "trackfeed": ["t", {"Value": "1"}]})
    st.handle("datafeed", ["t", 0, {"lines": [{"driver": {"RacingNumber": "1"}, "position": {"Value": "1"}}]}])
    snap = st.snapshot()
    assert snap["track"] == "Green" and snap["kind"] == "race"
    assert (snap["rows"][0]["pos"], snap["rows"][0]["code"], snap["rows"][0]["gap"]) == (1, "CAM", "+0.7")


def test_event_entry_lists_saved(tmp_path):
    store = Store(tmp_path / "t.db")
    ev = Event("2027-wrc-rallye-monte-carlo", 2027, "Rallye Monte-Carlo",
               sessions=[Session("day1", "Day 1 (Thu)", "rally_day", day=date(2027, 1, 21))],
               entrants=["Elfyn Evans", "Scott Martin"])
    unknown = Event("2027-wrc-rally-sweden", 2027, "Rally Sweden",
                    sessions=[Session("day1", "Day 1 (Thu)", "rally_day", day=date(2027, 2, 18))])
    store.save("wrc", AdapterResult([ev, unknown], [2027]))
    rows = store.entry_lists_with_events()
    assert {(r["event_key"], r["name"]) for r in rows} == {("2027-wrc-rallye-monte-carlo", "Elfyn Evans"),
                                                          ("2027-wrc-rallye-monte-carlo", "Scott Martin")}  # Sweden: not out
    assert all(r["first_seen"] == "" for r in rows)  # first import of a series is the baseline, not news

    # A list published later is news; names already seen keep their original first_seen.
    sweden = Event("2027-wrc-rally-sweden", 2027, "Rally Sweden",
                   sessions=[Session("day1", "Day 1 (Thu)", "rally_day", day=date(2027, 2, 18))], entrants=["Elfyn Evans"])
    store.save("wrc", AdapterResult([ev, sweden], [2027]))
    seen = {(r["event_key"], r["name"]): r["first_seen"] for r in store.entry_lists_with_events()}
    assert seen[("2027-wrc-rallye-monte-carlo", "Elfyn Evans")] == ""
    assert seen[("2027-wrc-rally-sweden", "Elfyn Evans")].startswith("20")



def test_follow_status_finds_drivers_anywhere(tmp_path):
    from lightsout import follow
    from lightsout.models import Entry
    store = Store(tmp_path / "t.db")
    soon = datetime.now(timezone.utc).replace(microsecond=0)
    f1 = Event("r1", soon.year, "Next GP", sessions=[Session("fp1", "Practice 1", PRACTICE, start=soon.replace(year=soon.year + 1))])
    store.save("f1", AdapterResult([f1], [soon.year], [Entry("Max Verstappen", "Red Bull")]))
    lm = Event("lm", soon.year + 1, "24 Hours of Le Mans",
               sessions=[Session("race", "Race", RACE, start=soon.replace(year=soon.year + 1, month=6, day=13))],
               entrants=["Max Verstappen"])
    store.save("wec", AdapterResult([lm], [soon.year + 1]))
    (st,) = follow.status(store, ["Max Verstapen"])  # misspelt on purpose
    assert set(st["series"]) == {"f1", "wec"}
    assert {f["kind"] for f in st["found"]} == {"grid", "entry"}
    assert follow.status(store, ["Lia Block"])[0]["found"] == []


def test_nascar_races_and_live():
    from lightsout.adapters import nascar
    from lightsout.live.nascar import NascarState
    (ev,) = nascar.parse_races([{
        "race_id": 5629, "race_season": 2026, "race_name": "Bank of America 400", "track_name": "Charlotte Motor Speedway",
        "race_date": "2026-10-11T15:00:00", "television_broadcaster": "USA",
        "schedule": [{"event_name": "Garage Hours", "start_time_utc": "2026-10-10T09:00:00", "run_type": 0},
                     {"event_name": "Race", "start_time_utc": "2026-10-11T19:00:00", "run_type": 3}]}])
    (race,) = ev.sessions  # garage hours and meetings are skipped
    assert race.kind == RACE and race.tv == "USA" and (race.end - race.start).seconds == 4 * 3600
    st = NascarState("cup")
    st.apply({"series_id": 2, "vehicles": []})  # an O'Reilly session doesn't belong to Cup
    assert not st.raw
    st.apply({"series_id": 1, "run_type": 3, "lap_number": 230, "laps_in_race": 267, "flag_state": 2, "vehicles": [
        {"running_position": 1, "vehicle_number": "19", "driver": {"full_name": "Chase Briscoe (C)"}, "laps_completed": 230, "status": 1},
        {"running_position": 2, "vehicle_number": "24", "driver": {"full_name": "William Byron"}, "laps_completed": 76, "status": 2}]})
    snap = st.snapshot()
    assert snap["track"] == "Caution" and snap["rows"][0]["name"] == "Chase Briscoe"
    assert snap["rows"][1]["gap"] == "-154 laps" and snap["rows"][1]["status"] == "OUT"


def test_watch_prefers_session_network():
    from lightsout.watch import watch_for
    assert watch_for("cup", RACE, "PRIME VIDEO")["badge"] == "PAID"
    assert watch_for("cup", RACE, "USA")["label"] == "USA Network"
    assert watch_for("noaps", PRACTICE)["label"] == "The CW"


# ---- GT World Challenge Europe ------------------------------------------------------------------
def _gtwce(name):
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / name).read_text()


def test_gtwce_calendar_past_and_upcoming():
    from lightsout.adapters import gtwce
    page = _gtwce("gtwce_calendar.html")
    rounds = gtwce.parse_past(page) + gtwce.parse_calendar(page)
    assert [r["round"] for r in rounds] == [f"Round {n}" for n in range(1, 11)]  # test days skipped
    by = {r["name"]: r for r in rounds}
    assert by["Magny-Cours"]["start"] == date(2026, 7, 30) and by["Magny-Cours"]["end"] == date(2026, 8, 2)  # across months
    assert by["Portimao"]["cup"] == "Endurance Cup" and by["Portimao"]["path"] == "/event/255/portimao"


def test_gtwce_timetable_uses_gmt():
    from lightsout.adapters import gtwce
    sessions = gtwce.parse_timetable(_gtwce("gtwce_event.html"), 2026, "Endurance Cup", "Portimao")
    race = next(s for s in sessions if s.kind == RACE)
    assert race.name == "Main Race" and race.start == datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc)
    assert race.end - race.start == timedelta(hours=3)
    assert not any("Bronze" in s.name for s in sessions)


def test_gtwce_entry_list_finds_verstappen(tmp_path):
    from lightsout import follow
    from lightsout.adapters import gtwce
    from lightsout.db import Store
    rows = gtwce.parse_entry_list(_gtwce("gtwce_entrylist.html"))
    car3 = next(r for r in rows if r["number"] == "3")
    assert car3["drivers"] == ["Max Verstappen", "Ben Barnicoat", "Chris Lulham"] and car3["class"] == "Pro"
    sessions = gtwce.parse_timetable(_gtwce("gtwce_event.html"), 2026, "Endurance Cup", "Portimao")
    ev = Event(key="2026-portimao", season=2026, name="Portimao (Endurance)", sessions=sessions,
               entrants=[d for r in rows for d in r["drivers"]])
    store = Store(tmp_path / "t.db")
    store.save("gtwce", AdapterResult([ev], [2026]))
    st = follow.status(store, ["Max Verstappen"])[0]
    assert "gtwce" in st["series"] and any(f["kind"] == "entry" and f["series"] == "gtwce" for f in st["found"])


def test_ticket_links_per_series():
    from lightsout import watch
    assert watch.tickets_for("f2")["url"] == "https://tickets.formula1.com/en"  # F2 races on F1 weekends
    assert watch.tickets_for("wec", "https://www.fiawec.com/en/race/x")["url"] == "https://www.fiawec.com/en/race/x"
    assert watch.tickets_for("ara") is None


def test_person_page(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("LIGHTSOUT_DATA", str(tmp_path))
    monkeypatch.setenv("LIGHTSOUT_NO_REFRESH", "1")
    monkeypatch.setenv("LIGHTSOUT_ADMIN_EMAIL", "owner@example.com")
    from lightsout import access, app as app_module
    importlib.reload(access)
    app_module = importlib.reload(app_module)
    monkeypatch.setattr(access, "email_from", lambda headers: "owner@example.com")
    future = datetime.now(timezone.utc) + timedelta(days=5)
    ev = Event(key="2099-x", season=future.year, name="Test GP", sessions=[
        Session("fp1", "Practice 1", PRACTICE, start=future), Session("race", "Race", RACE, start=future + timedelta(days=2))],
        entrants=["Max Verstappen"])
    app_module.store.save("gtwce", AdapterResult([ev], [future.year], [Entry(name="Max Verstappen", team="Team V", number="3")]))
    from fastapi.testclient import TestClient
    with TestClient(app_module.app) as c:
        p = c.get("/api/person/Max Verstappen").json()
    assert p["series"] == ["gtwce"] and p["rides"][0]["team"] == "Team V"
    assert [u["name"] for u in p["upcoming"]] == ["Race"]  # practice left out
    assert p["upcoming"][0]["tickets"]["url"].startswith("https://www.gt-world-challenge-europe.com")


def test_f1_race_ticket_pages():
    from lightsout import tickets
    pages = [{"slug": "united states", "url": "U"}, {"slug": "las vegas", "url": "V"}, {"slug": "brazil", "url": "B"}]
    assert tickets.f1_page_for({"name": "United States Grand Prix", "venue": "Circuit of the Americas"}, pages) == "U"
    assert tickets.f1_page_for({"name": "Las Vegas Grand Prix", "country": "USA"}, pages) == "V"
    assert tickets.f1_page_for({"name": "São Paulo Grand Prix"}, pages) == "B"
    assert tickets.f1_page_for({"name": "Miami Grand Prix", "country": "United States"}, pages) is None  # not Austin's page
    assert tickets.parse_store('<a href="/en/f1-3320-united-states">x</a>')[0]["url"].endswith("f1-3320-united-states")
    assert tickets.parse_store("<html>waiting room</html>") == []  # the queue page changes nothing


def test_support_series_use_the_gp_weekend_page(monkeypatch):
    from lightsout import tickets
    monkeypatch.setattr(tickets, "f1_pages", [{"slug": "united states", "url": "U"}])
    f1 = [{"name": "Singapore Grand Prix", "start_day": "2026-10-09", "end_day": "2026-10-11"},
          {"name": "United States Grand Prix", "start_day": "2026-10-23", "end_day": "2026-10-25"}]
    t = tickets.for_event("f1a", {"name": "Austin", "start_day": "2026-10-22", "end_day": "2026-10-24"}, f1)
    assert t == {"url": "U", "label": "F1 tickets: United States Grand Prix"}
    assert tickets.for_event("f2", {"name": "Somewhere", "start_day": "2026-07-01", "end_day": "2026-07-03"}, f1)["url"] \
        == "https://tickets.formula1.com/en"  # no matching weekend: the store's front page


# ---- What's new ---------------------------------------------------------------------------------
def test_changelog_matches_changes_yaml():
    """CHANGELOG.md is generated: if this fails, run .venv/bin/python tools/changelog.py."""
    import importlib.util
    from pathlib import Path
    from lightsout import changes
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("changelog_tool", root / "tools" / "changelog.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert (root / "CHANGELOG.md").read_text() == changes.markdown(changes.load(), tool.APP, tool.SITE)


def test_changes_are_well_formed():
    from lightsout import changes
    entries = changes.load()
    assert entries and len({e["id"] for e in entries}) == len(entries)
    assert [e["date"] for e in entries] == sorted((e["date"] for e in entries), reverse=True)
    assert all(e["kind"] in ("new", "improved", "fixed") for e in entries)


# ---- NLS ----------------------------------------------------------------------------------------------
def _nls(name):
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def test_nls_calendar_page():
    from lightsout.adapters import nls
    page = _nls("nls_calendar_2026.json")[0]["content"]["rendered"]
    evs = {e.key: e for e in nls.parse_calendar(nls._text(page), "u")}
    assert len(evs) == 9
    assert evs["2026-nls7"].sessions[0].name == "6h race"            # no "(6h)" suffix on that line
    assert evs["2026-nls8"].name == "NLS8 · ADAC Reinoldus-Langstreckenrennen"  # not swallowed by NLS7
    q = evs["2026-adac-24h-qualifiers"]
    assert [s.name for s in q.sessions] == ["4h race 1", "4h race 2"] and q.sessions[1].day == date(2026, 4, 19)


def test_nls_next_season_from_announcement():
    from lightsout.adapters import nls
    post = _nls("nls_calendar_2027_post.json")[0]["content"]["rendered"]
    evs = nls.parse_announcement(nls._text(post), "u")
    assert [e.key for e in evs] == [f"2027-nls{n}" for n in range(1, 9)]  # the double-header is two events
    assert evs[0].sessions[0].day == date(2027, 3, 20) and evs[3].sessions[0].name == "6h race"
    assert evs[5].sessions[0].day == date(2027, 9, 12)


def test_every_series_has_a_type_and_official_site():
    from lightsout.series import SERIES
    for s in SERIES:
        assert s.category in {"single", "sportscar", "stock", "rally", "bikes"}, s.id
        assert s.site.startswith("https://"), s.id
        assert not s.timing or s.timing.startswith("https://"), s.id


def test_venue_list_is_sane_and_matches_this_seasons_tracks():
    from lightsout import weather
    seen = {}
    for p in weather.places():
        assert -90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180, p["name"]
        for t in p["terms"]:
            assert t not in seen, f"{t!r} is in both {seen.get(t)} and {p['name']}"
            seen[t] = p["name"]
    cases = {
        ("Circuit of the Americas", "United States Grand Prix"): "Circuit of the Americas",
        ("Americas", "Austin E-Prix"): "Circuit of the Americas",
        ("Homestead-Miami Speedway", "NASCAR Championship Race"): "Homestead-Miami Speedway",
        ("Miami IA", "Miami E-Prix"): "Miami International Autodrome",
        ("Las Vegas Strip Street Circuit", "Las Vegas Grand Prix"): "Las Vegas Strip Circuit",
        ("Monte Carlo", "Monaco E-Prix"): "Monaco",
        ("", "Rallye Monte-Carlo"): "Rallye Monte-Carlo (Gap)",
        ("Jarama", "Madrid E-Prix"): "Jarama",
        ("TotalEnergies 6 Hours of Spa-Francorchamps", ""): "Spa-Francorchamps",
        ("Nürburgring Nordschleife", "NLS1"): "Nurburgring",
        ("Autódromo José Carlos Pace", "Brazilian Grand Prix"): "Interlagos",
        ("", "Rally Sweden"): "Rally Sweden (Umea)",
    }
    for (venue, name), want in cases.items():
        assert (weather.place_for(venue, name) or {}).get("name") == want, (venue, name)
    assert weather.place_for("Barcelona", "Rally Spain") and not weather.place_for("", "Somewhere Unknown")


def test_forecast_picks_the_start_hour_or_the_day():
    from datetime import datetime, timezone
    from lightsout.weather import forecast_for
    data = {"utc_offset_seconds": -18000,
            "hourly": {"time": ["2026-10-25T13:00", "2026-10-25T14:00"], "temperature_2m": [24.1, 25.3],
                       "precipitation_probability": [10, 40], "weather_code": [2, 61]},
            "daily": {"time": ["2026-10-25"], "temperature_2m_max": [27.0], "temperature_2m_min": [15.0],
                      "precipitation_probability_max": [45], "weather_code": [61]}}
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    # 19:00 UTC = 14:00 in Austin (UTC-5)
    assert forecast_for({"start_utc": "2026-10-25T19:00:00Z"}, data, now) == {"c": 25.3, "rain": 40, "code": 61}
    assert forecast_for({"day": "2026-10-25"}, data, now) == {"hi": 27.0, "lo": 15.0, "rain": 45, "code": 61}
    assert forecast_for({"start_utc": "2026-11-20T19:00:00Z"}, data, now) is None  # more than 7 days out


def test_event_keywords_skip_generic_words():
    from lightsout.app import _keywords
    assert _keywords({"name": "United States Grand Prix", "venue": "Circuit of the Americas"}) >= {"austin", "americas"}
    assert "grand" not in _keywords({"name": "Brazilian Grand Prix", "venue": "Interlagos"})


def test_gtwce_standings_and_results():
    from lightsout.adapters import gtwce as g
    rows = g.parse_standings(_gtwce("gtwce_standings_drivers.html"))
    assert rows[0][0] == 1 and rows[0][0] == rows[1][0] and rows[0][2] == rows[1][2]   # a crew shares a row each
    known = {"lucas auer": ("Lucas Auer", "Winward Racing")}
    crew = g.crews(rows, known)[0]
    assert " / " in crew.name and crew.team == "Winward Racing"
    rounds = g.parse_results_index(_gtwce("gtwce_results_index.html"), 2026)
    assert rounds[0].endswith("circuit-paul-ricard") and not any("test" in r for r in rounds)
    page = _gtwce("gtwce_results_round.html")
    assert g.round_name(page) == "Barcelona"
    assert g.race_sessions(page, "/results/2026/barcelona") == ["race-1", "race-2"]
    res = g.result_rows(g.parse_classification(page))
    assert res[0].pos == "1" and res[0].time.endswith("laps") and res[0].team.startswith("#69 ")
    assert res[1].time.startswith("+") and res[-1].pos == res[-1].time == "NC"


def test_motogp_calendar_per_class():
    import json
    from pathlib import Path
    from lightsout.adapters import motogp
    data = json.loads((Path(__file__).parent / "fixtures" / "motogp_events.json").read_text())
    ev = motogp.parse_events(data, "motogp")
    assert len(ev) == 1 and ev[0].key == "2026-aus" and ev[0].name == "Grand Prix of Australia"  # tests left out
    kinds = [s.kind for s in ev[0].sessions]
    assert "sprint" in kinds and kinds[-1] == "race"
    race = ev[0].sessions[-1]
    assert race.end > race.start  # the feed lists races with end = start
    moto3 = motogp.parse_events(data, "moto3")[0]
    assert "sprint" not in [s.kind for s in moto3.sessions] and moto3.sessions[0].start.tzinfo
    assert motogp.gp_name("RED BULL GRAND PRIX OF THE UNITED STATES ") == "Grand Prix of the United States"


def test_motogp_classification():
    import json
    from pathlib import Path
    from lightsout.adapters import motogp
    rows = motogp.parse_classification(json.loads((Path(__file__).parent / "fixtures" / "motogp_classification.json").read_text()))
    assert rows[0].pos == "1" and rows[0].time and rows[1].time.startswith("+")
    assert rows[-1].pos == "DNF" and "lap" in rows[-1].time


def test_sro_twelve_hour_clock_and_race_lengths():
    from datetime import timedelta
    from lightsout.adapters import gtwce
    page = ('<table class="timetable__table"><caption><span>Friday, 25 September</span></caption><tbody>'
            '<tr><td>Practice 1</td><td>04:45 pm</td><td>09:45 pm</td></tr>'
            '<tr><td>Driver Autograph Session</td><td>05:45 pm</td><td>10:45 pm</td></tr>'
            '<tr><td>Race</td><td>11:30 pm</td><td>04:30 am</td></tr></tbody></table>')
    s = gtwce.parse_timetable(page, 2026, "", "Barber Motorsports Park", timedelta(minutes=90))
    assert [x.name for x in s] == ["Practice 1", "Race"]  # off-track rows dropped
    assert s[0].start.hour == 21 and s[1].start.day == 26 and s[1].end - s[1].start == timedelta(minutes=90)
    assert gtwce.race_length("Meguiar's Bathurst 12 Hour", "") == timedelta(hours=12)
    assert gtwce.race_length("Suzuka 1000km", "") == timedelta(hours=6)
    assert gtwce.race_length("Portimao", "Endurance Cup") == timedelta(hours=3)


def test_sro_entry_list_matched_by_words():
    from lightsout.adapters import gtwce
    paths = ["/entry-list/2026/indianpolis-8-hour", "/entry-list/2026/road-atlanta", "/entry-list/2025/road-atlanta"]
    rd = {"path": "/event/115/michelin-raceway-road-atlanta", "name": "Michelin Raceway Road Atlanta"}
    assert gtwce.match_entry_list(rd, 2026, paths) == "/entry-list/2026/road-atlanta"
    assert gtwce.match_entry_list({"path": "/event/9/sonoma-raceway", "name": "Sonoma Raceway"}, 2026, paths) is None
