"""The Dakar: reading Wikipedia's article, the stage card, alerts and the stage-winner Pick'em."""
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from lightsout import dakar
from lightsout.db import Store
from lightsout.models import AdapterResult, Event, Session

FIX = Path(__file__).parent / "fixtures"
D26 = dakar.parse((FIX / "dakar_2026.wiki").read_text())
D27 = dakar.parse((FIX / "dakar_2027.wiki").read_text())


def _store(tmp_path, data=D26, year=2026) -> Store:
    s = Store(tmp_path / "t.db")
    dakar.init(s.conn)
    start, end = (date(2026, 1, 3), date(2026, 1, 17)) if year == 2026 else (date(2027, 1, 9), date(2027, 1, 23))
    ev = Event(key=f"{year}-dakar-rally", season=year, name="Dakar Rally", venue="", country="", start_day=start,
               end_day=end, sessions=[Session("event", "Event (timetable TBA)", "event", day=start, end_day=end)])
    s.save("w2rc", AdapterResult([ev], [year], []))
    s.save_cached(dakar.key(year), {"year": year, "revid": 1, "url": dakar.article_url(year), **data})
    return s


def test_reads_the_stage_list():
    assert len(D27["stages"]) == 15
    first, transfer, rest, last = D27["stages"][0], D27["stages"][2], D27["stages"][8], D27["stages"][-1]
    assert (first["label"], first["day"], first["start"], first["special_km"]) == ("Prologue", "2027-01-09", "Loulé", 29)
    assert (transfer["kind"], transfer["finish"]) == ("transfer", "Bouârfa")
    assert (rest["kind"], rest["start"], rest["finish"]) == ("rest", "Agadir", "Agadir")
    assert (last["label"], last["finish"], last["total_km"]) == ("Stage 12", "Marrakesh", 240)
    four = next(s for s in D26["stages"] if s["label"] == "Stage 4")
    assert four["note"] == "marathon" and four["special_km"] == 417


def test_reads_stage_results_winners_and_final():
    bikes = D26["results"]["Bikes"]["Stage 1"]
    assert [r["name"] for r in bikes["stage"]] == ["Edgar Canet", "Daniel Sanders", "Ricky Brabec"]
    assert bikes["stage"][1]["gap"] == "00:01:02" and bikes["overall"][0]["time"] == "03:27:42"
    assert D26["results"]["Cars"]["Prologue"]["overall"] == []        # the prologue didn't count overall for cars
    assert D26["winners"]["Stage 5"]["Cars"] == "Nani Roma"
    cars = D26["final"]["Cars"]
    assert len(cars) == 10 and (cars[0]["name"], cars[0]["co"], cars[0]["make"]) == ("Nasser Al-Attiyah", "Fabian Lurquin", "Dacia Sandrider")
    assert D26["final"]["Bikes"][0]["name"] == "Luciano Benavides" and D26["final"]["Bikes"][1]["gap"] == "00:00:02"
    assert D27["results"] == {} and D27["classes"] == []


def test_cell_cleanup():
    assert dakar.clean("'''{{flagicon|ESP}} [[Edgar Canet i Ardèvol|Edgar Canet]]'''<ref name=x/>") == "Edgar Canet"
    assert dakar.stage_label("4 (marathon)") == "Stage 4" and dakar.stage_label("Rest Day") == "Rest day"


def test_summary_today_and_latest(tmp_path):
    s = _store(tmp_path)
    d = dakar.summary(s, datetime(2026, 1, 8, 12, tzinfo=timezone.utc))
    assert d["today"]["label"] == "Stage 5" and d["latest"] == "Stage 13"   # the saved copy is the finished rally
    assert d["results"]["Bikes"]["overall"][0]["name"] == "Luciano Benavides"
    assert d["stages"][1]["winners"] == {"Bikes": "Edgar Canet", "Cars": "Guillaume De Mévius"}
    assert d["final"]["Cars"][0]["name"] == "Nasser Al-Attiyah"


def test_summary_before_results(tmp_path):
    s = _store(tmp_path, D27, 2027)
    d = dakar.summary(s, datetime(2027, 1, 11, 12, tzinfo=timezone.utc))
    assert d["today"]["kind"] == "transfer" and d["latest"] is None and d["results"] == {}


def test_alerts_once_and_only_fresh(tmp_path):
    s = _store(tmp_path)
    d = {**D26, "year": 2026}
    assert dakar.news(d, s, date(2026, 1, 17)) == []                        # first sight: wait for a second read
    items = dakar.news(d, s, date(2026, 1, 17))
    assert [i["stage"]["label"] for i in items] == ["Stage 12", "Stage 13"]   # older stages are marked, not sent
    assert items[-1]["classes"] == ["Bikes", "Cars"]
    assert dakar.news(d, s, date(2026, 1, 17)) == items                     # until the loop marks them sent
    for i in items:
        s.mark_sent(i["keys"])
    assert dakar.news(d, s, date(2026, 1, 17)) == []
    # Cars posted later than Bikes: the second push (same tag) repeats the bike winner.
    d2 = {**d, "results": {**d["results"], "Cars": {k: v for k, v in d["results"]["Cars"].items() if k != "Stage 13"}},
          "winners": {k: {c: n for c, n in v.items() if not (k == "Stage 13" and c == "Cars")} for k, v in d["winners"].items()},
          "year": 1999}
    dakar.news(d2, s, date(2026, 1, 17))
    [only] = [i for i in dakar.news(d2, s, date(2026, 1, 17)) if i["stage"]["label"] == "Stage 13"]
    assert only["classes"] == ["Bikes"]
    # A vandal swaps the winner for one read, reverted before the next: nothing goes out.
    d3 = {**d, "year": 1998}
    dakar.news(d3, s, date(2026, 1, 17))
    for i in dakar.news(d3, s, date(2026, 1, 17)):
        s.mark_sent(i["keys"])
    bad = {**d3, "results": {**d3["results"], "Bikes": {**d3["results"]["Bikes"], "Stage 13": {"stage": [{"pos": 1, "name": "Some Vandal", "make": "", "time": "", "gap": ""}], "overall": []}}}}
    assert dakar.news(bad, s, date(2026, 1, 17)) == []
    assert dakar.news(d3, s, date(2026, 1, 17)) == []
    msg = dakar.alert_text(d, items[-1], {dakar.normalise("Nasser Al-Attiyah")}, {"Cars": "Mattias Ekström"})
    assert msg["title"] == "Dakar · Stage 13"
    assert "Cars: Mattias Ekström (Ford). Al-Attiyah leads overall · your pick Ekström +1" in msg["body"]
    assert "Your drivers: Nasser Al-Attiyah P1 overall" in msg["body"]
    assert "Al-Attiyah P1 overall" in dakar.alert_text(d, items[-1], {dakar.normalise("Al-Attiyah")}, {})["body"]


def test_km_columns():
    one = [{"text": "390", "colspan": 1}, {"text": "490", "colspan": 1}]
    split = [{"text": "420", "colspan": 1}, {"text": "430", "colspan": 1}, {"text": "600", "colspan": 1}, {"text": "610", "colspan": 1}]
    spanned = [{"text": "23", "colspan": 2}, {"text": "98", "colspan": 2}, {"text": "-", "colspan": 1}]
    assert dakar._kms(one) == (390, 490) and dakar._kms(split) == (420, 600) and dakar._kms(spanned) == (23, 98)


def test_stage_picks(tmp_path):
    s = _store(tmp_path, D27, 2027)
    s.save_cached(dakar.key(2026), {"year": 2026, **D26})          # last year's top ten = the field before stage 1
    now = datetime(2027, 1, 9, 12, tzinfo=timezone.utc)
    v = dakar.view(s, "a@x", {"a@x": "Ann", "b@x": "Bo"}, None, now)
    assert v["stage"]["label"] == "Stage 1" and v["prev"] == "Prologue"   # the prologue closed this morning
    bikes = v["classes"][0]
    assert bikes["class"] == "Bikes" and bikes["field"][0]["name"] == "Luciano Benavides"
    dakar.set_pick(s, "a@x", 2027, v["stage"], "Bikes", "Ricky Brabec", now)
    with pytest.raises(ValueError):
        dakar.set_pick(s, "a@x", 2027, v["stage"], "Bikes", "Somebody Else", now)
    with pytest.raises(ValueError):
        dakar.set_pick(s, "a@x", 2027, dakar.choose(dakar._stages(s, 2027), "prologue", now), "Bikes", "Ricky Brabec", now)
    assert dakar.view(s, "a@x", {"a@x": "Ann"}, "stage-1", now)["classes"][0]["mine"] == "Ricky Brabec"
    # Stage 1 posted: Brabec won it.
    data = s.cached(dakar.key(2027))[0]
    data["winners"] = {"Stage 1": {"Bikes": "Ricky Brabec", "Cars": "Carlos Sainz"}}
    s.save_cached(dakar.key(2027), data)
    later = datetime(2027, 1, 10, 18, tzinfo=timezone.utc)
    v = dakar.view(s, "a@x", {"a@x": "Ann"}, "stage-1", later)
    assert v["stage"]["closed"] and v["classes"][0]["points"] == 1
    assert dakar.board(s, 2027, {"a@x": "Ann"}, "a@x") == [{"name": "Ann", "pts": 1, "picked": 1, "me": True}]


def test_pickem_tab_window(tmp_path):
    s = _store(tmp_path, D27, 2027)
    assert not dakar.open_window(s, date(2026, 12, 20))
    assert dakar.open_window(s, date(2026, 12, 27)) and dakar.open_window(s, date(2027, 2, 2))
    assert not dakar.open_window(s, date(2027, 2, 3))
