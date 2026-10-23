import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lightsout import odds

spec = importlib.util.spec_from_file_location("odds_fetch", Path(__file__).parent.parent / "tools" / "odds_fetch.py")
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


def test_fetcher_parses_a_league():
    data = {"events": [{"id": "e1", "name": "YellaWood 500 2026", "startEventDate": "2026-10-25T18:00:00.0000000Z"}],
            "markets": [{"id": "m1", "eventId": "e1", "name": "Winner"}, {"id": "m2", "eventId": "e1", "name": "Empty"}],
            "selections": [{"marketId": "m1", "label": "Joey Logano", "trueOdds": 12.0, "displayOdds": {"american": "+1100"}},
                           {"marketId": "m1", "label": "Ryan Blaney", "trueOdds": 10.0, "displayOdds": {"american": "+900"}},
                           {"marketId": "m1", "label": "No price"}]}
    (ev,) = fetcher.parse_league(data, "cup")
    assert ev["start"] == "2026-10-25T18:00:00Z" and [m["name"] for m in ev["markets"]] == ["Winner"]
    assert ev["markets"][0]["rows"] == [{"name": "Ryan Blaney", "american": "+900", "pct": 10.0},
                                       {"name": "Joey Logano", "american": "+1100", "pct": 8.3}]
    assert fetcher.series_for("NASCAR Cup Series") == "cup" and fetcher.series_for("NASCAR Cup Series Championship") is None
    assert fetcher.series_for("NHRA Drag Racing") is None


def _write(tmp_path, fetched):
    f = tmp_path / "odds.json"
    f.write_text(json.dumps({"source": "DraftKings", "fetched": fetched, "events": [
        {"series": "f1", "name": "F1 United States GP 2026", "start": "2026-10-23T17:30:00Z", "markets": [
            {"name": "Winner", "rows": [{"name": "Max Verstappen", "american": "+110", "pct": 47.6}]},
            {"name": "Podium Finish", "rows": [{"name": "Max Verstappen", "american": "-360", "pct": 78.3}]}]}]}))
    os.utime(f)
    odds.setup(tmp_path)


def test_event_and_driver_odds(tmp_path):
    _write(tmp_path, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    ev = odds.for_event("f1", "2026-10-23", "2026-10-25")
    assert ev["markets"][0]["name"] == "Race Winner"
    assert odds.for_event("f1", "2026-11-06", "2026-11-08") is None and odds.for_event("cup", "2026-10-23", "2026-10-25") is None
    assert odds.winner(ev, "VERSTAPPEN Max") == {"market": "Race Winner", "american": "+110", "pct": 47.6}
    assert [x["market"] for x in odds.for_driver(ev, "Max Verstappen")] == ["Race Winner", "Podium Finish"]


def test_stale_odds_are_hidden(tmp_path):
    _write(tmp_path, (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert odds.for_event("f1", "2026-10-23", "2026-10-25") is None


def test_middle_names_and_initials():
    rows = [{"name": "Austin Hill"}, {"name": "John Hunter Nemechek"}, {"name": "Kyle Busch"}]
    assert odds.find(rows, "Austin J Hill")["name"] == "Austin Hill"
    assert odds.find(rows, "John H. Nemechek")["name"] == "John Hunter Nemechek"
    assert odds.find(rows, "Kurt Busch") is None


def test_duels_use_the_market_that_separates_the_drivers():
    ev = {"markets": [
        {"name": "Race Winner", "rows": [{"name": "Max Verstappen", "american": "+110", "pct": 47.6},
                                          {"name": "George Russell", "american": "+1000", "pct": 9.1},
                                          {"name": "Liam Lawson", "american": "+50000", "pct": 0.2},
                                          {"name": "Arvid Lindblad", "american": "+50000", "pct": 0.2},
                                          {"name": "Pierre Gasly", "american": "+50000", "pct": 0.2},
                                          {"name": "Franco Colapinto", "american": "+50000", "pct": 0.2}]},
        {"name": "Top 10 Finish", "rows": [{"name": "Liam Lawson", "american": "-120", "pct": 54.5},
                                            {"name": "Arvid Lindblad", "american": "+150", "pct": 40.0},
                                            {"name": "Pierre Gasly", "american": "+300", "pct": 25.0},
                                            {"name": "Franco Colapinto", "american": "+300", "pct": 25.0}]}]}
    a, b = odds.duel(ev, "George Russell", "Max Verstappen")
    assert (a["market"], a["american"], b["american"]) == ("Race Winner", "+1000", "+110")
    a, b = odds.duel(ev, "Liam Lawson", "Arvid Lindblad")
    assert (a["market"], a["pct"], b["pct"]) == ("Top 10 Finish", 54.5, 40.0)
    assert odds.duel(ev, "Pierre Gasly", "Franco Colapinto") is None
