"""Driver cards: team colours and the per-round history behind season form."""
from lightsout import colors, refresh
from lightsout.db import Store


def test_team_colours_longest_whole_word_wins():
    assert colors.for_team("Red Bull KTM Factory Racing", "motogp") == colors.for_team("KTM", "motogp")
    assert colors.for_team("Red Bull Racing", "f1") != colors.for_team("Racing Bulls", "f1")
    assert colors.for_team("#24 · Chevrolet", "cup") == colors.for_team("Chevrolet")
    # "ART" (the F2 team) must not paint Aston Martin; no match falls back to the series colour.
    assert colors.for_team("Aston Martin", "f1") != colors.for_team("ART Grand Prix", "f2")
    assert colors.for_team("Some Privateer", "wrc") == "#ffcc00"


def test_rounds_keep_their_first_day_and_take_newer_results(tmp_path):
    s = Store(tmp_path / "t.db")
    s.save_round("cup", 2026, "Bank of America 400", "2026-10-12", {"A Driver": ["1", 0]})       # unofficial
    s.save_round("cup", 2026, "Earlier 500", "2026-10-05", {"A Driver": ["7", 30]})
    s.save_round("cup", 2026, "Bank of America 400", "2026-10-14", {"A Driver": ["1", 55]})      # official, later
    rounds = s.rounds("cup", 2026)
    assert [r["event"] for r in rounds] == ["Earlier 500", "Bank of America 400"]
    assert rounds[1]["day"] == "2026-10-12" and rounds[1]["rows"]["A Driver"] == ["1", 55]
    assert s.round_events("cup", 2026) == {"Earlier 500", "Bank of America 400"}


def test_season_from_label():
    assert refresh.season_of("2025 · after round 24") == 2025
