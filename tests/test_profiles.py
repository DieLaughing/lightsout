import asyncio

from lightsout import profiles
from lightsout.db import Store


def test_clean_drops_pronunciation_keeps_birth():
    assert profiles._clean("Álex Palou Montalbo (Catalan pronunciation: [ˈal]; born 1 April 1997) is a driver.\nMore") \
        == "Álex Palou Montalbo (born 1 April 1997) is a driver."
    assert profiles._clean("Oliver James Bearman (; born 8 May 2005) is") == "Oliver James Bearman (born 8 May 2005) is"


def test_wikipedia_page_must_be_a_racing_person_of_that_name():
    page = {"pageid": 1, "title": "Álex Palou", "description": "Spanish racing driver (born 1997)"}
    assert profiles._is_driver(page, "Alex Palou", None)
    assert not profiles._is_driver({**page, "title": "2026 IndyCar Series", "description": "American auto racing season"},
                                   "Alex Palou", None)
    assert not profiles._is_driver({"pageid": 2, "title": "Campos Racing", "description": "Spanish motor racing team"},
                                   "Campos Racing", None)
    assert profiles._is_driver({"pageid": 3, "title": "Kalle Rovanperä (rally driver)", "description": "Finnish rally driver"},
                               "Kalle Rovanpera", None)


def _race(season, name, pos, grid, pts, team="McLaren", fl=None):
    r = {"positionText": str(pos), "position": str(pos) if str(pos).isdigit() else "20", "grid": str(grid),
         "points": str(pts), "Constructor": {"name": team}}
    if fl:
        r["FastestLap"] = {"rank": "1"}
    return {"season": str(season), "raceName": name, "Results": [r]}


def test_f1_career_totals(tmp_path, monkeypatch):
    races = [_race(2024, "Bahrain GP", 5, 3, 10), _race(2024, "Saudi GP", 1, 1, 25, fl=True),
             _race(2025, "Australian GP", 2, 1, 18, team="Ferrari"), _race(2025, "Chinese GP", "R", 4, 0, team="Ferrari")]
    answers = {
        "drivers/x/results.json?limit=100&offset=0": {"total": "4", "RaceTable": {"Races": races}},
        "drivers/x/sprint.json?limit=100": {"RaceTable": {"Races": [{"SprintResults": [{"points": "8"}]}]}},
        "2025/drivers/x/driverstandings.json": {"StandingsTable": {"StandingsLists": [{"DriverStandings": [
            {"positionText": "1", "points": "26", "wins": "0"}]}]}},
        "2024/drivers/x/driverstandings.json": {"StandingsTable": {"StandingsLists": [{"DriverStandings": [
            {"positionText": "1", "points": "35", "wins": "1"}]}]}},
    }

    async def fake(store, path, forever=False):
        return answers[path]
    monkeypatch.setattr(profiles, "_jolpica_get", fake)

    class Year(profiles.datetime):
        @classmethod
        def now(cls, tz=None):
            return profiles.datetime(2025, 6, 1, tzinfo=tz)
    monkeypatch.setattr(profiles, "datetime", Year)
    c = asyncio.run(profiles._f1_career(Store(tmp_path / "t.db"), "x"))
    assert (c["races"], c["wins"], c["podiums"], c["poles"], c["fastest_laps"], c["points"]) == (4, 1, 2, 2, 1, 61)
    assert c["titles"] == [2024]          # 2025 isn't over, so leading it isn't a title
    assert c["first_win"] == {"season": 2024, "race": "Saudi GP"}
    assert [s["season"] for s in c["seasons"]] == [2025, 2024] and c["seasons"][0]["teams"] == ["Ferrari"]


def test_get_answers_at_once_and_builds_in_the_background(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")

    async def build(store_, name, key):
        store_.save_cached(key, {"wiki": {"title": name}, "f1": None})
    monkeypatch.setattr(profiles, "_build", build)

    async def main():
        first = profiles.get(store, "Lia Block")
        await asyncio.wait(list(profiles._building.values()))
        return first, profiles.get(store, "Lia Block")
    first, then = asyncio.run(main())
    assert first == {"pending": True}
    assert then == {"wiki": {"title": "Lia Block"}, "f1": None, "pending": False}


def test_photo_paths_are_only_our_own_files(tmp_path):
    profiles.setup(tmp_path)
    assert profiles.photo_path("0123456789abcdef.jpg") == tmp_path / "img" / "d" / "0123456789abcdef.jpg"
    assert profiles.photo_path("../lightsout.db") is None and profiles.photo_path("x.svg") is None
