import json

from lightsout import f1a, news
from lightsout.adapters import f1academy
from lightsout.db import Store
from lightsout.live.fia import FiaState
from lightsout.models import Standings, StandingRow
from lightsout.names import Tagger


def _page(data: dict) -> str:
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": {"pageData": data}}})}</script>'


def _driver(i, name, backer, cc="GB", country="Great Britain"):
    return {"DriverId": i, "FullName": name, "TLA": name.split()[-1][:3].upper(), "CarNumber": i, "support": backer,
            "CountryCode": cc, "CountryName": country}


ROSTER = _page({"Teams": [
    {"TeamFullName": "Campos Racing", "Drivers": [_driver(42, "Alisha  Palmowski", "Red Bull Racing ")]},
    {"TeamFullName": "Hitech", "Drivers": [_driver(73, "Alexia Danielsson (WCD)", "TeamViewer", "SE", "Sweden"),
                                           _driver(67, "Ava Dobson", "American Express", "US", "United States")]},
]})


def test_roster_cleans_names_and_flags_wild_cards():
    rows = {r["name"]: r for r in f1academy.parse_roster(ROSTER)}
    assert set(rows) == {"Alisha Palmowski", "Alexia Danielsson", "Ava Dobson"}
    assert rows["Alexia Danielsson"]["wildcard"] and not rows["Ava Dobson"]["wildcard"]
    assert rows["Alisha Palmowski"]["backer"] == "Red Bull Racing"
    assert [e.name for e in f1academy.parse_drivers(ROSTER)][1] == "Alexia Danielsson"


def test_profile_bio_drops_link_lists_and_keeps_the_quote():
    synopsis = ("__Nina has a new home.__\n\nBorn in [Wijster](https://x), she karted.\n\n>  Home team helps.\n\n"
                "### CATCH UP WITH NINA:\n\n- [Story](https://y)\n")
    p = f1academy.parse_profile(_page({
        "DriverId": 40, "FullName": "Nina  Gademan", "Nationality": "Dutch", "CountryName": "Netherlands",
        "CurrentDriverTeam": {"TeamName": "MP Motorsport", "CarNumber": 3, "Driver_Type": "Driver"},
        "LatestStandings": {"Position": 3, "TotalPoints": 71}, "TeamMates": [{"FullName": "Alba Larsen"}],
        "Content": {"driverSynopsis": synopsis, "support": "Alpine", "dateOfBirth": "2003-08-30",
                    "driverImage": {"url": "https://res.cloudinary.com/a/image/upload/v1/n.jpg"},
                    "driverInstagram": "https://instagram.com/n"},
        "Articles": [{"id": "abc", "slug": "s", "title": "A  story", "updatedAt": "2026-10-05T00:00:00.000Z"}]}))
    assert p["bio"] == ["Nina has a new home.", "Born in Wijster, she karted."]
    assert p["quote"] == "Home team helps."
    assert (p["name"], p["backer"], p["team"], p["number"], p["pos"], p["wildcard"]) == \
        ("Nina Gademan", "Alpine", "MP Motorsport", "3", 3, False)
    assert p["articles"][0] == {"title": "A story", "url": "https://www.f1academy.com/Latest/abc/s", "time": "2026-10-05T00:00:00.000Z"}
    assert p["links"] == {"instagram": "https://instagram.com/n"}


def test_backers_that_are_f1_teams():
    assert f1a.f1_team("Red Bull Racing") == "Red Bull Racing"
    assert f1a.f1_team("Haas F1 Team") == "Haas"
    assert f1a.f1_team("LEGO") is None and f1a.f1_team("McLaren Oxagon") is None


def test_tag_and_who_to_watch(tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_cached(f1a.KEY, f1academy.parse_roster(ROSTER))
    rows = f1a.tag(store, [{"name": "Alisha Palmowski"}, {"name": "Alexia Danielsson"}, {"name": "Nobody"}], colour=True)
    assert rows[0]["f1_team"] == "Red Bull Racing" and rows[0]["f1_color"] and rows[0]["colour"]
    assert rows[1]["wildcard"] and rows[1]["f1_team"] is None and rows[1]["backer"] == "TeamViewer"
    assert rows[2] == {"name": "Nobody"}
    st = Standings(drivers=[StandingRow(1, "Alisha Palmowski", "Campos Racing", 120), StandingRow(2, "Ava Dobson", "Hitech", 80)])
    store.save_standings("f1a", st)
    w = f1a.watch(store, {"country": "United States"}, {"cc": "us"})
    assert [d["name"] for d in w["leaders"]] == ["Alisha Palmowski", "Ava Dobson"]
    assert [d["name"] for d in w["home"]] == ["Ava Dobson"]
    assert [d["name"] for d in w["wildcards"]] == ["Alexia Danielsson"]


def test_f1a_news_page_is_tagged():
    tagger = Tagger([{"series": "f1a", "name": "Alisha Palmowski", "team": "Campos Racing", "number": "21"}])
    page = _page({"items": [{"id": "x1", "slug": "pal", "title": "Palmowski  on pole", "description": "<p>Alisha Palmowski wins.</p>",
                             "updatedAt": "2026-10-22T17:04:27.364Z"}, {"id": "x2", "title": "no slug"}]})
    (item,) = news.parse_f1a(page, tagger)
    assert item["url"] == "https://www.f1academy.com/Latest/x1/pal" and item["source"] == "F1 ACADEMY"
    assert item["time"] == "2026-10-22T17:04:27Z" and item["series"] == ["f1a"] and item["drivers"] == ["Alisha Palmowski"]


def test_live_wild_card_name_and_no_pit_after_the_flag():
    st = FiaState("f1a")
    st.load({"data": ["t", {"Session": "Qualifying"}, [
        {"driver": {"RacingNumber": "45", "TLA": "DAN", "FullName": "ALEXIA DANIELSSON (WCD)"}, "position": {"Value": "15"},
         "gap": {"Value": "+1.533"}, "status": {"InPit": True}}]]})
    row = st.snapshot()["rows"][0]
    assert (row["name"], row["wildcard"], row["status"]) == ("Alexia Danielsson", True, "PIT")
    st.handle("sessionfeed", ["t", {"Value": "Finalised"}])
    assert st.snapshot()["rows"][0]["status"] == ""
