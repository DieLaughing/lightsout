"""Winter in the paddock: when a series counts as between seasons, and what its card carries."""
from datetime import date, datetime, time, timedelta, timezone

from lightsout import paddock
from lightsout.db import Store
from lightsout.models import PRACTICE, RACE, AdapterResult, Event, Session, Standings, StandingRow

NOW = datetime(2026, 12, 10, 12, tzinfo=timezone.utc)


def _event(key: str, season: int, day: date, name: str | None = None, kind: str = RACE) -> Event:
    start = datetime.combine(day, time(14), timezone.utc)
    return Event(key=key, season=season, name=name or f"Round {key}", venue="Circuit", country="USA", start_day=day,
                 end_day=day + timedelta(days=2), sessions=[Session("race", "Race", kind, start=start + timedelta(days=2))])


def _store(tmp_path, series: str, events: list[Event], season_label: str = "2026") -> Store:
    s = Store(tmp_path / "t.db")
    s.save(series, AdapterResult(events, sorted({e.season for e in events}), []))
    s.save_standings(series, Standings(drivers=[StandingRow(pos=1, name="Lando Norris", team="McLaren", points=400)],
                                       teams=[StandingRow(pos=1, name="McLaren", points=700)],
                                       last_event="Final", season_label=season_label))
    _saved_at(s, NOW)
    return s


def _saved_at(s: Store, when: datetime) -> None:
    with s.conn:
        s.conn.execute("UPDATE standings SET updated=?", (when.strftime("%Y-%m-%dT%H:%M:%SZ"),))


def test_between_seasons_shows_champion_next_race_and_tests(tmp_path):
    s = _store(tmp_path, "f1", [_event("final", 2026, date(2026, 12, 4)), _event("opener", 2027, date(2027, 3, 12), "Bahrain GP")])
    [w] = paddock.winter(s, ["f1"], NOW)
    assert w["champion"]["driver"]["name"] == "Lando Norris" and w["champion"]["team"]["name"] == "McLaren"
    assert w["next"]["event"] == "Bahrain GP" and w["next"]["season"] == 2027
    assert [t["place"] for t in w["tests"]] == ["Bahrain"]   # from data/testing.yaml


def test_mid_season_and_summer_breaks_are_not_winter(tmp_path):
    s = _store(tmp_path, "f1", [_event("a", 2026, date(2026, 12, 4)), _event("b", 2026, date(2027, 1, 8))])
    assert paddock.winter(s, ["f1"], NOW) == []            # a 5-week gap inside one season
    s = _store(tmp_path, "fe", [_event("a", 2026, date(2026, 8, 1)), _event("b", 2026, date(2027, 1, 8))])
    [w] = paddock.winter(s, ["fe"], NOW)                    # a long gap is winter even with one season number
    assert w["champion"] is None                            # ...but the season isn't over, so no champion


def test_racing_returns_inside_lead_days(tmp_path):
    s = _store(tmp_path, "f1", [_event("a", 2026, date(2026, 9, 1)), _event("b", 2027, NOW.date() + timedelta(days=5))])
    assert paddock.winter(s, ["f1"], NOW) == []


def test_no_calendar_yet_and_no_races_yet(tmp_path):
    s = _store(tmp_path, "cup", [_event("a", 2026, date(2026, 11, 1))])
    [w] = paddock.winter(s, ["cup"], NOW)
    assert w["next"] is None and w["champion"]["driver"]["name"] == "Lando Norris"
    # Only next season on file (the source dropped the finished one): winter, champion from the older table.
    s = _store(tmp_path, "indycar", [_event("b", 2027, date(2027, 3, 7))])
    [w] = paddock.winter(s, ["indycar"], NOW)
    assert w["season"] == 2026 and w["champion"]["season"] == "2026"
    s = _store(tmp_path, "fe", [_event("c", 2026, date(2027, 1, 15))], season_label="2025-26")
    assert paddock.winter(s, ["fe"], NOW)[0]["champion"]["season"] == "2025-26"
    assert paddock.winter(Store(tmp_path / "empty.db"), ["wec"], NOW) == []


def test_champion_from_a_label_without_a_year_until_it_resets(tmp_path):
    s = _store(tmp_path, "cup", [_event("a", 2026, date(2026, 11, 1))], season_label="Points")
    assert paddock.winter(s, ["cup"], NOW)[0]["champion"]["season"] == "2026"
    s.save_standings("cup", Standings(drivers=[StandingRow(pos=1, name="Kyle Larson", team="Hendrick", points=0)], season_label="Points"))
    _saved_at(s, NOW)
    assert paddock.winter(s, ["cup"], NOW)[0]["champion"] is None


def test_champion_only_from_that_seasons_table(tmp_path):
    s = _store(tmp_path, "f1", [_event("a", 2026, date(2026, 12, 4))], season_label="2025 · after round 24")
    assert paddock.winter(s, ["f1"], NOW)[0]["champion"] is None


def test_schedule_tests_count_and_past_tests_drop(tmp_path):
    events = [_event("a", 2026, date(2026, 10, 11)), _event("roar", 2027, date(2027, 1, 16), "Roar Before the Rolex 24", PRACTICE),
              _event("old-test", 2026, date(2026, 11, 1), "Winter test", PRACTICE), _event("b", 2027, date(2027, 1, 22), "Rolex 24")]
    s = _store(tmp_path, "imsa", events)
    assert [t["name"] for t in paddock.winter(s, ["imsa"], NOW)[0]["tests"]] == ["Roar Before the Rolex 24"]


def test_moves_are_kept_longer_than_the_wire_and_filtered(tmp_path):
    s = Store(tmp_path / "t.db")
    item = lambda i, title, days, **kw: {"id": f"n{i}", "time": (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),  # noqa: E731
                                         "title": title, "url": f"https://example.com/{i}", "source": "X", "series": ["f1"],
                                         "drivers": ["Oliver Bearman"], "teams": [], **kw}
    news = [item(1, "Bearman signs new Haas deal for 2027", 40), item(2, "Bearman takes pole in Abu Dhabi", 10),
            item(3, "Haas confirms 2027 line-up", 2), item(4, "Who joins whom", 3, drivers=[]),
            item(5, "Ancient signing story", 200)]
    assert paddock.remember(s, news, NOW) == 2
    assert paddock.remember(s, news, NOW) == 0     # nothing new, nothing rewritten
    assert [m["id"] for m in s.cached(paddock.MOVES_KEY)[0]] == ["n3", "n1"]
    assert paddock._moves(s.cached(paddock.MOVES_KEY)[0], "f1", {"day": "2026-12-06"}) == [m for m in s.cached(paddock.MOVES_KEY)[0] if m["id"] == "n3"]
    assert paddock._moves(s.cached(paddock.MOVES_KEY)[0], "cup", {"day": "2026-12-06"}) == []


def test_move_headlines():
    yes = ["Colapinto to replace Doohan at Alpine", "Tsunoda leaves Red Bull", "Larson re-signs with Hendrick", "Ogier confirms 2027 seat"]
    no = ["Verstappen extends lead", "Norris wins in Vegas", "Leclerc tops practice", "Ferrari's new floor explained"]
    assert all(paddock.is_move({"title": t, "drivers": ["x"]}) for t in yes)
    assert not any(paddock.is_move({"title": t, "drivers": ["x"]}) for t in no)


def test_a_race_under_way_is_not_winter(tmp_path):
    # Day two of a two-week rally-raid (one EVENT session with no timetable), next one a year away.
    dakar = Event(key="d27", season=2027, name="Dakar Rally", venue="", country="SA", start_day=date(2027, 1, 2),
                  end_day=date(2027, 1, 16), sessions=[Session("all", "Dakar Rally", "event", day=date(2027, 1, 2), end_day=date(2027, 1, 16))])
    s = _store(tmp_path, "w2rc", [_event("a", 2026, date(2026, 10, 1)), dakar, _event("d28", 2028, date(2028, 1, 2))])
    assert paddock.winter(s, ["w2rc"], datetime(2027, 1, 3, 12, tzinfo=timezone.utc)) == []


def test_no_champion_from_the_table_saved_before_the_finale(tmp_path):
    s = _store(tmp_path, "f1", [_event("final", 2026, date(2026, 12, 4)), _event("opener", 2027, date(2027, 3, 12))])
    _saved_at(s, datetime(2026, 12, 5, tzinfo=timezone.utc))
    assert paddock.winter(s, ["f1"], NOW)[0]["champion"] is None


def test_test_events_match_whole_words(tmp_path):
    events = [_event("a", 2026, date(2026, 10, 11)), _event("x", 2027, date(2027, 1, 10), "Fastest Lap Contest", PRACTICE),
              _event("b", 2027, date(2027, 3, 1))]
    assert paddock.winter(_store(tmp_path, "imsa", events), ["imsa"], NOW)[0]["tests"] == []
