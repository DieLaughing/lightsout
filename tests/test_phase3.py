"""Phase 3: name tagging, race events from each live feed, news parsing, settings on the account, alerts.
NASCAR fixtures are trimmed copies of the 2026-10-11 Cup race at Charlotte."""
import asyncio
import copy
import importlib
import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
ENTRIES = [
    {"series": "f1", "name": "Max Verstappen", "team": "Red Bull", "number": "3"},
    {"series": "f1", "name": "Lando Norris", "team": "McLaren", "number": "4"},
    {"series": "f1", "name": "Oscar Piastri", "team": "McLaren", "number": "81"},
    {"series": "f1", "name": "Isack Hadjar", "team": "RB F1 Team", "number": "6"},
    {"series": "indycar", "name": "Will Power", "team": "Andretti Global", "number": "26"},
    {"series": "cup", "name": "Kyle Larson", "team": "Chevrolet", "number": "5"},
    {"series": "noaps", "name": "Kyle Larson", "team": "Chevrolet", "number": "88"},
    {"series": "cup", "name": "Joey Logano", "team": "Ford", "number": "22"},
    {"series": "fe", "name": "Mitch Evans", "team": "JAGUAR TCS RACING", "number": "9"},
]


def load(name):
    return json.loads((FIX / name).read_text())


# ---- tagging ------------------------------------------------------------------------------
def test_tagging_full_names_surnames_and_teams():
    from lightsout.names import Tagger
    t = Tagger(ENTRIES)
    assert t.tag("Verstappen jokes as Red Bull phases in a new engineer", ["f1"]) == (["Max Verstappen"], ["Red Bull Racing"])
    assert t.tag("Hadjar on the Racing Bulls seat", ["f1"]) == (["Isack Hadjar"], ["Racing Bulls"])
    assert t.tag("Larson wins again", ["cup"])[0] == ["Kyle Larson"]  # same person in two series counts once
    # "Power" is also an everyday word: needs the full name.
    assert t.tag("New power unit rules for 2027", ["indycar"])[0] == []
    assert t.tag("Will Power to Andretti", ["indycar"])[0] == ["Will Power"]


def test_mixed_feed_team_follows_the_drivers_series():
    from lightsout.names import Tagger
    t = Tagger(ENTRIES)
    drivers, teams = t.tag("Logano looks for a boost with Team Penske")
    assert drivers == ["Joey Logano"] and teams == ["Team Penske"]  # not Formula E's DS Penske


# ---- race control classification -------------------------------------------------------------
def test_race_control_classification():
    from lightsout.live.events import car_numbers, classify, tidy
    assert classify("FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 4 (NOR) - CAUSING A COLLISION") == "penalty"
    assert classify("TURN 1 INCIDENT INVOLVING CARS 4 (NOR) AND 81 (PIA) UNDER INVESTIGATION") == "investigation"
    assert classify("SAFETY CAR DEPLOYED", "SafetyCar") == "caution"
    assert classify("CHEQUERED FLAG", "Flag", "CHEQUERED") is None  # not a red flag
    assert classify("RED FLAG", "Flag", "RED") == "red_flag"
    assert classify("DRS ENABLED", "Drs") is None
    assert classify("YELLOW IN TRACK SECTOR 7", "Flag", "YELLOW") is None
    assert car_numbers("INCIDENT INVOLVING CARS 4 (NOR) AND 81 (PIA)") == ["4", "81"]
    assert tidy("FIA STEWARDS: CAR 4 (NOR)").startswith("FIA stewards: car 4 (NOR)")


def test_f1_events_from_feed_changes():
    from lightsout.live.f1 import F1State
    st = F1State()
    st.apply("SessionInfo", {"Key": 1, "Type": "Race", "Name": "Race", "Meeting": {"Name": "Test GP"}})
    st.apply("DriverList", {"3": {"FullName": "MAX VERSTAPPEN"}, "4": {"FullName": "LANDO NORRIS"}})
    st.apply("SessionStatus", {"Status": "Started"})
    st.apply("TimingData", {"Lines": {"3": {"Position": "1"}, "4": {"Position": "2"}}})
    assert st.log.take() == []  # the first picture after connecting isn't news
    st.apply("RaceControlMessages", {"Messages": [{"Utc": "2026-10-11T12:00:00", "Lap": 5, "Category": "Other",
                                                   "Message": "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 4 (NOR)"}]})
    st.apply("TimingData", {"Lines": {"4": {"Position": "1"}, "3": {"Position": "2"}}})
    st.apply("LapCount", {"CurrentLap": 6})
    st.apply("TimingData", {"Lines": {"3": {"Retired": True}}})
    st.apply("SessionStatus", {"Status": "Finished"})
    kinds = [(e["kind"], e["names"]) for e in st.log.take()]
    assert ("penalty", ["Lando Norris"]) in kinds
    assert ("lead", ["Lando Norris"]) in kinds
    assert ("retired", ["Max Verstappen"]) in kinds
    assert any(k == "result" for k, _ in kinds)


def test_nascar_events_cautions_notes_and_outs():
    from lightsout.live.nascar import NascarState
    feed = load("nascar_feed.json")
    st = NascarState("cup")
    early = copy.deepcopy(feed)
    early["flag_state"], early["lap_number"], early["stage"] = 1, 70, {"stage_num": 1}
    for v in early["vehicles"]:
        v["status"] = 1
    st.apply(early)
    st.apply(feed)
    st.apply_flags(load("nascar_flags.json"))
    st.apply_notes(load("nascar_notes.json"))
    ev = st.log.take()
    cautions = [e for e in ev if e["kind"] == "caution"]
    assert any("incident turn 4" in e["text"] and e["numbers"] == ["54", "6"] for e in cautions)  # not "4" from "turn 4"
    assert any(e["kind"] == "retired" for e in ev) and any(e["kind"] == "result" for e in ev)
    notes = [e for e in ev if e["kind"] == "note"]
    assert notes and not any(e["important"] for e in notes)  # play-by-play stays off the Wire
    again = NascarState("cup")
    again.apply(early)
    again.apply_flags(load("nascar_flags.json"))
    assert {e["id"] for e in again.log.take() if e["kind"] == "caution"} == {e["id"] for e in cautions}  # stable ids


def test_indycar_dnf_with_reason():
    from lightsout.live.indycar import IndyState
    def feed(status, flag="GREEN", lap="10"):
        return {"timing_results": {"heartbeat": {"SessionType": "R", "currentFlag": flag, "lapNumber": lap, "EventID": "1",
                                                 "EventSessionID": "2", "eventName": "Test", "SessionName": "Race"},
                                   "Item": [{"rank": 1, "no": "26", "firstName": "Will", "lastName": "Power", "status": "Active"},
                                            {"rank": 2, "no": "19", "firstName": "Dennis", "lastName": "Hauger",
                                             "status": status, "comment": "Contact"}]}}
    st = IndyState()
    st.apply(feed("Active"))
    st.apply(feed("DNF", flag="YELLOW", lap="11"))
    ev = st.log.take()
    assert any(e["kind"] == "retired" and "(Contact)" in e["text"] for e in ev)
    assert any(e["kind"] == "caution" for e in ev)


# ---- news ------------------------------------------------------------------------------------
def test_news_parse_tags_and_drops_uncovered_series():
    from lightsout import news
    from lightsout.names import Tagger
    xml = """<rss><channel>
      <item><title>Verstappen wins in Singapore</title><link>https://x.test/a</link>
        <description><![CDATA[<p>Red Bull's driver held off Norris.</p>The post appeared first]]></description>
        <pubDate>Sun, 11 Oct 2026 14:00:00 GMT</pubDate></item>
      <item><title>MotoGP: Marquez crashes</title><link>https://x.test/b</link><pubDate>Sun, 11 Oct 2026 14:00:00 GMT</pubDate></item>
    </channel></rss>"""
    items = news.parse(xml, "RACER", None, None, Tagger(ENTRIES))
    assert len(items) == 1  # a mixed feed's story about a series we don't cover is dropped
    a = items[0]
    assert a["series"] == ["f1"] and "Max Verstappen" in a["drivers"] and "Red Bull Racing" in a["teams"]
    assert a["text"] == "Red Bull's driver held off Norris." and a["time"] == "2026-10-11T14:00:00Z"


# ---- app: settings on the account, Wire filters ------------------------------------------------
@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LIGHTSOUT_DATA", str(tmp_path))
    monkeypatch.setenv("LIGHTSOUT_NO_REFRESH", "1")
    monkeypatch.setenv("LIGHTSOUT_ADMIN_EMAIL", "owner@example.com")
    from lightsout import access, app as app_module
    importlib.reload(access)
    app_module = importlib.reload(app_module)
    monkeypatch.setattr(access, "email_from", lambda headers: "owner@example.com")
    from fastapi.testclient import TestClient
    with TestClient(app_module.app) as c:
        yield c, app_module


def test_settings_keep_only_what_was_sent(app_client):
    c, _ = app_client
    assert c.get("/api/me").json()["settings"] is None  # the browser's copy moves in on first visit
    c.put("/api/settings", json={"follows": [{"name": "Lia Block"}], "series": ["f1", "nope"]})
    st = c.get("/api/me").json()["settings"]
    assert st == {"follows": [{"name": "Lia Block"}], "series": ["f1"]}  # no empty "kinds" overwriting defaults
    c.put("/api/settings", json={"show": {"wec": "qualifying", "cup": "races", "nope": "races", "f1": "all", "f2": "x"}})
    assert c.get("/api/me").json()["settings"]["show"] == {"wec": "qualifying", "cup": "races"}  # all = no entry


def test_wire_filters(app_client):
    c, mod = app_client
    mod.store.add_news([{"id": "n1", "time": "2026-10-11T10:00:00Z", "title": "Norris news", "url": "https://x/1",
                         "source": "S", "series": ["f1"], "drivers": ["Lando Norris"], "teams": ["McLaren"]}])
    mod.store.add_events([{"id": "e1", "series": "cup", "time": "2099-10-11T10:00:00Z", "kind": "caution", "important": True,
                           "text": "Caution, lap 5", "drivers": [], "teams": []},
                          {"id": "e2", "series": "cup", "time": "2099-10-11T10:00:01Z", "kind": "note", "important": False,
                           "text": "#5 leads", "drivers": [], "teams": []}])
    assert {i["id"] for i in c.get("/api/wire").json()} == {"n1", "e1"}  # notes stay off the Wire
    assert [i["id"] for i in c.get("/api/wire?series=cup").json()] == ["e1"]
    assert [i["id"] for i in c.get("/api/wire?team=McLaren").json()] == ["n1"]
    c.put("/api/settings", json={"follows": [{"name": "Lando Norris"}]})
    assert [i["id"] for i in c.get("/api/wire?mine=1").json()] == ["n1"]


# ---- alerts ------------------------------------------------------------------------------------
class FakePusher:
    def __init__(self):
        self.sent = []

    async def send(self, sub, payload):
        self.sent.append(payload)
        return "ok"


def _alerts(tmp_path, follows):
    from lightsout import alerts
    from lightsout.db import Store
    store = Store(tmp_path / "a.db")
    store.set_status("fan@x.com", "approved")
    store.save_settings("fan@x.com", {"follows": [{"name": n} for n in follows]})
    store.add_push("fan@x.com", {"endpoint": "https://push.test/1", "keys": {"p256dh": "k", "auth": "a"}}, "t")
    with store.conn:
        store.conn.executemany("INSERT INTO entry (series, name, team, number) VALUES (?,?,?,?)",
                               [(e["series"], e["name"], e["team"], e["number"]) for e in ENTRIES])
    p = FakePusher()
    return alerts.Alerts(store, p, "https://lo.test"), p


def _event(kind, drivers=(), series="f1", minutes_ago=1, eid=None):
    from datetime import datetime, timedelta, timezone
    t = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": eid or f"{series}:{kind}:{minutes_ago}", "series": series, "kind": kind, "text": f"{kind} text",
            "time": t, "drivers": list(drivers), "teams": [], "important": True}


def test_alerts_personal_and_race_wide(tmp_path):
    al, p = _alerts(tmp_path, ["Lando Norris"])
    asyncio.run(al.on_events([_event("penalty", ["Lando Norris"]), _event("penalty", ["Max Verstappen"], eid="other"),
                              _event("caution", eid="sc"), _event("caution", series="cup", eid="cupsc")]))
    titles = [x["title"] for x in p.sent]
    assert any("Lando Norris: penalty" in t for t in titles)
    assert not any("Max Verstappen" in t for t in titles)          # not followed
    assert sum("caution" in t.lower() or "F1:" in t for t in titles) == 1  # F1 caution yes, NASCAR caution no
    asyncio.run(al.on_events([_event("penalty", ["Lando Norris"])]))
    assert len(p.sent) == len(titles)                               # never twice


def test_alerts_skip_old_history(tmp_path):
    al, p = _alerts(tmp_path, ["Lando Norris"])
    asyncio.run(al.on_events([_event("retired", ["Lando Norris"], minutes_ago=90)]))
    assert p.sent == []  # a reconnect replays history; that isn't news


def test_alerts_respect_switches(tmp_path):
    al, p = _alerts(tmp_path, ["Lando Norris"])
    al.store.save_settings("fan@x.com", {"follows": [{"name": "Lando Norris"}], "notify": {"incidents": False}})
    asyncio.run(al.on_events([_event("penalty", ["Lando Norris"])]))
    assert p.sent == []


def test_nascar_timing_results_fill_last_race_until_official_results():
    from lightsout.adapters.nascar import parse_timing_results
    feed = {"vehicles": [
        {"running_position": 2, "laps_completed": 266, "delta": -1, "status": 1, "vehicle_number": "20", "vehicle_manufacturer": "Tyt",
         "driver": {"full_name": "Christopher Bell (C)"}},
        {"running_position": 1, "laps_completed": 267, "status": 1, "vehicle_number": "19", "vehicle_manufacturer": "Tyt",
         "driver": {"full_name": "Chase Briscoe (C)"}},
        {"running_position": 3, "laps_completed": 76, "status": 3, "vehicle_number": "5", "vehicle_manufacturer": "Chv",
         "driver": {"full_name": "Kyle Larson"}},
    ]}
    feed["vehicles"].append({"running_position": 4, "laps_completed": 267, "delta": 4.9, "status": 1, "vehicle_number": "12",
                             "vehicle_manufacturer": "Frd", "driver": {"full_name": "Ryan Blaney"}})
    feed["vehicles"][1]["delta"] = 0
    rows = parse_timing_results(feed)
    assert [(r.pos, r.name, r.team, r.time) for r in rows] == [
        ("1", "Chase Briscoe", "#19 · Toyota", "Winner"),
        ("2", "Christopher Bell", "#20 · Toyota", "1 lap down"),
        ("3", "Kyle Larson", "#5 · Chevrolet", "Out (lap 76)"),
        ("4", "Ryan Blaney", "#12 · Ford", "+4.900"),
    ]


def test_places_labels_and_every_track_has_a_town():
    from lightsout import places, weather
    for p in weather.places():
        assert p.get("town") and str(p.get("cc")) in places.COUNTRIES, p["name"]
    lab = {p["name"]: places.label(p) for p in weather.places()}
    assert lab["Michelin Raceway Road Atlanta"] == "Braselton, GA"
    assert lab["Phillip Island"] == "Phillip Island, VIC"
    assert lab["Red Bull Ring"] == "Spielberg, Austria"
    assert lab["Marina Bay"] == "Singapore"  # town = country: said once
    assert places.for_event("Road America", "Road America")["slug"] == "road-america"
    assert 590 < places.miles((33.75, -84.39), (30.13, -97.64)) < 860  # Atlanta to COTA, roughly


def test_track_page_and_home_setting(app_client):
    from datetime import datetime, timedelta, timezone
    from lightsout.models import AdapterResult, Event, Session
    c, mod = app_client
    soon = datetime.now(timezone.utc) + timedelta(days=3)
    for sid, name in (("indycar", "Grand Prix of Road America"), ("gtwcam", "Road America")):
        ev = Event(key=f"{soon.year}-ra", season=soon.year, name=name, venue="Road America",
                   sessions=[Session("race", "Race", "race", start=soon)]).normalised()
        mod.store.save(sid, AdapterResult([ev], [soon.year]))
    t = c.get("/api/track/road-america").json()
    assert t["label"] == "Elkhart Lake, WI" and {e["series"] for e in t["events"]} == {"indycar", "gtwcam"}
    assert len(t["sessions"]) == 2 and t["sessions"][0]["place"]["slug"] == "road-america"
    tracks = c.get("/api/tracks").json()
    assert any(x["slug"] == "road-america" and x["next"] for x in tracks)
    assert c.get("/api/track/nowhere").status_code == 404
    c.put("/api/settings", json={"home": {"label": "Ocala, FL", "lat": 29.18765, "lon": -82.1401}, "nearMiles": 500})
    st = c.get("/api/me").json()["settings"]
    assert st["home"] == {"label": "Ocala, FL", "lat": 29.19, "lon": -82.14} and st["nearMiles"] == 500
    c.put("/api/settings", json={"home": {"lat": "x"}, "nearMiles": 7})
    st = c.get("/api/me").json()["settings"]
    assert st["home"] is None and st["nearMiles"] == 300
