"""Pick'em: duels drawn from the standings, picks closing at the start, scoring from Last race."""
from datetime import datetime, timedelta, timezone

import pytest

from lightsout import picks
from lightsout.db import Store
from lightsout.models import RACE, AdapterResult, Event, Session, Standings, StandingRow, ResultRow

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
START = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)

DRIVERS = [("Andrea Kimi Antonelli", "Mercedes", 340), ("George Russell", "Mercedes", 238), ("Lewis Hamilton", "Ferrari", 229),
           ("Max Verstappen", "Red Bull", 221), ("Charles Leclerc", "Ferrari", 215), ("Lando Norris", "McLaren", 200),
           ("Oscar Piastri", "McLaren", 190), ("Isack Hadjar", "Red Bull", 60), ("Alex Albon", "Williams", 40),
           ("Carlos Sainz", "Williams", 38), ("Lance Stroll", "Aston Martin", 10), ("Fernando Alonso", "Aston Martin", 12)]


def standings(results: list[tuple[str, str]], event: str = "Singapore Grand Prix") -> Standings:
    team = {n: t for n, t, _ in DRIVERS}
    return Standings(
        drivers=[StandingRow(i + 1, n, t, p) for i, (n, t, p) in enumerate(DRIVERS)],
        last_event=event, last_session="Race",
        last_results=[ResultRow(pos, n, team[n]) for pos, n in results])


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    picks.init(s.conn)
    ev = Event("2026-r18", 2026, "United States Grand Prix", venue="Circuit of the Americas",
               sessions=[Session("race", "Race", RACE, start=START)])
    s.save("f1", AdapterResult(events=[ev], seasons=[2026]))
    s.save_standings("f1", standings([(str(i + 1), n) for i, (n, _, _) in enumerate(DRIVERS)]))
    return s


def test_team_of_drops_nascar_car_number():
    assert picks.team_of("#19 · Joe Gibbs Racing") == "Joe Gibbs Racing"
    assert picks.team_of("Red Bull") == "Red Bull"


def test_duels_are_close_and_nobody_appears_twice(store):
    field = picks.field_from(store.standings("f1"))
    duels = picks.make_duels(field)
    assert len(duels) == picks.DUELS
    names = [n for d in duels for n in d]
    assert len(names) == len(set(names))
    team = {n: t for n, t, _ in DRIVERS}
    assert sum(team[a] == team[b] for a, b in duels) <= picks.TEAMMATE_DUELS
    assert ["Lewis Hamilton", "Charles Leclerc"] in duels           # 14 points apart, teammates
    assert ["Lance Stroll", "Fernando Alonso"] not in duels and ["Fernando Alonso", "Lance Stroll"] not in duels  # back of the field


def test_same_race_matches_loosely():
    assert picks.same_race("Bahrain Grand Prix", {"name": "Bahrain Grand Prix in Malaysia", "venue": ""})
    assert picks.same_race("Hollywood Casino 400", {"name": "Hollywood Casino 400 ", "venue": "Kansas Speedway"})
    assert not picks.same_race("Singapore Grand Prix", {"name": "United States Grand Prix", "venue": "Circuit of the Americas"})


def test_duel_and_podium_scoring():
    order = [{"pos": "1", "name": "A"}, {"pos": "2", "name": "B"}, {"pos": "3", "name": "C"},
             {"pos": "R", "name": "D"}, {"pos": "R", "name": "E"}]
    assert picks.duel_outcome(order, ["B", "A"], "A") == "won"
    assert picks.duel_outcome(order, ["C", "D"], "D") == "lost"        # a retirement loses to a finisher
    assert picks.duel_outcome(order, ["D", "E"], "D") == "void"        # both out
    assert picks.duel_outcome(order, ["A", "Z"], "A") == "void"        # Z didn't race
    assert picks.podium_points(order, 1, "A") == 2
    assert picks.podium_points(order, 1, "B") == 1
    assert picks.podium_points(order, 3, "D") == 0


def test_slate_opens_ten_days_out_and_freezes(store):
    race = picks.races(store, "f1")[0]
    assert picks.slate(store, "f1", race, NOW - timedelta(days=10)) is None
    sl = picks.slate(store, "f1", race, NOW)
    store.save_standings("f1", standings([("1", "Lance Stroll")]))  # standings move; the duels don't
    assert picks.slate(store, "f1", race, NOW)["duels"] == sl["duels"]


def test_pick_lock_podium_and_grading(store):
    race = picks.races(store, "f1")[0]
    sl = picks.slate(store, "f1", race, NOW)
    a, b = sl["duels"][0]
    picks.set_pick(store, "me@x", sl, "duel", 0, a, lock=True, now=NOW)
    picks.set_pick(store, "pal@x", sl, "duel", 0, b, now=NOW)
    picks.set_pick(store, "me@x", sl, "duel", 1, sl["duels"][1][0], lock=True, now=NOW)  # moves the lock
    with pytest.raises(ValueError):
        picks.set_pick(store, "me@x", sl, "duel", 2, "Somebody Else", now=NOW)
    picks.set_pick(store, "me@x", sl, "podium", 1, a, now=NOW)
    picks.set_pick(store, "me@x", sl, "podium", 2, a, now=NOW)  # same driver again: moves to P2
    picks.set_pick(store, "me@x", sl, "podium", 1, b, now=NOW)
    with pytest.raises(ValueError):
        picks.set_pick(store, "me@x", sl, "duel", 0, b, now=START)

    names = {"me@x": "Me", "pal@x": "Pal"}
    before = picks.view(store, "me@x", sl, names, NOW)
    assert before["duels"][0]["everyone"] is None and before["duels"][0]["count"] == 2
    assert before["podium"]["mine"] == [b, a, None]
    assert [d["lock"] for d in before["duels"][:2]] == [False, True]

    # Last week's result (saved before the start) must not score this race.
    assert picks.grade(store, "f1", START + timedelta(hours=3)) == 0
    order = [a, b] + [n for n, _, _ in DRIVERS if n not in (a, b)]
    store.save_standings("f1", standings([(str(i + 1), n) for i, n in enumerate(order)], "United States Grand Prix"))
    with store.conn:  # saved after the race (the real clock is earlier than these test dates)
        store.conn.execute("UPDATE standings SET updated=? WHERE series='f1'", (picks._iso(START + timedelta(hours=2)),))
    assert picks.grade(store, "f1", START + timedelta(hours=3)) == 5

    after = picks.view(store, "me@x", picks.slate(store, "f1", race, START + timedelta(hours=3)), names, START + timedelta(hours=3))
    assert after["duels"][0]["outcome"] == "won" and after["duels"][0]["points"] == 1
    assert [p["name"] for p in after["duels"][0]["everyone"]] == ["Pal"]
    assert after["podium"]["points"] == [1, 1, None]   # B and A both on the podium, neither in the exact spot
    table = picks.board(store, "f1", 2026, names, "me@x")["table"]
    assert table[0]["name"] == "Me" and table[0]["podium"] == 2
    assert table[1] == {"name": "Pal", "me": False, "pts": 0, "w": 0, "l": 1, "podium": 0}
