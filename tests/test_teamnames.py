"""Team names: one spelling per team, the F1 seat a driver holds now, and SRO entry lists in either column order."""
from lightsout import teamnames
import base64
import json

from lightsout.adapters import f1, fia_feeder, gtwce
from lightsout.models import Entry, ResultRow, StandingRow, Standings


def test_spellings_and_whitespace():
    assert teamnames.tidy("f1", "RB F1 Team") == "Racing Bulls" and teamnames.tidy("f1", "Red Bull") == "Red Bull Racing"
    assert teamnames.tidy("fe", "JAGUAR TCS RACING") == "Jaguar TCS Racing"
    assert teamnames.tidy("moto2", "ELF Marc VDS Racing Team  ") == "ELF Marc VDS Racing Team"
    assert teamnames.tidy("wrc", "Rally1 · M-sport Ford World Rally Team") == "Rally1 · M-Sport Ford World Rally Team"
    # Tagging merges Toyota's two WRC entries; the app must still show them apart.
    assert teamnames.tidy("wrc", "Toyota Gazoo Racing WRT2") == "Toyota Gazoo Racing WRT2"
    assert teamnames.tidy("f2", "RB F1 Team") == "RB F1 Team"  # spellings belong to their series


def test_nascar_standings_name_the_team_and_the_make():
    st = Standings(drivers=[StandingRow(1, "Ross Chastain", "#1 · Chevrolet"), StandingRow(2, "Kyle Larson", "#5 · Chevrolet")],
                   last_results=[ResultRow("1", "Kyle Larson", "#5 ·  Hendrick Motorsports ")])
    teamnames.standings("cup", st, {"Ross Chastain": "#1 · Trackhouse Racing"})
    assert [r.team for r in st.drivers] == ["#1 · Trackhouse Racing · Chevrolet", "#5 · Hendrick Motorsports · Chevrolet"]
    assert st.last_results[0].team == "#5 · Hendrick Motorsports"
    assert teamnames.entries("f1", [Entry("Liam Lawson", "RB F1 Team")])[0].team == "Racing Bulls"


def test_f1_seat_comes_from_the_latest_race():
    row = {"Driver": {"driverId": "lawson"}, "Constructors": [{"name": "RB F1 Team"}, {"name": "Red Bull"}]}
    race = {"Results": [{"Driver": {"driverId": "lawson"}, "Constructor": {"name": "RB F1 Team"}}]}
    assert f1.current_team(row, f1.latest_teams(race)) == "RB F1 Team"
    swap = {"Results": [{"Driver": {"driverId": "lawson"}, "Constructor": {"name": "Red Bull"}}]}  # a one-race swap
    assert f1.current_team(row, f1.latest_teams(swap)) == "Red Bull"
    assert f1.current_team(row, {}) == "Red Bull"  # sat the last race out: the feed's last team


def test_sro_entry_lists_in_either_column_order():
    europe = ("<table><tr><th>Car #</th><th>Team</th><th>Driver 1</th><th>Driver 2</th><th>Car</th><th>Class</th></tr>"
              "<tr><td>3</td><td>TEAM WRT</td><td>Max VERSTAPPEN</td><td>Chris LULHAM</td><td>BMW</td><td>Pro</td></tr></table>")
    america = ("<table><tr><th>Car #</th><th>Driver 1</th><th>Driver 2</th><th>Team</th><th>Car</th><th>Class</th></tr>"
               "<tr><td>12</td><td>Matias Perez Companc***</td><td>Frederik Schandorff</td><td>AF Corse USA</td><td>Ferrari</td><td>Pro</td></tr></table>")
    assert gtwce.parse_entry_list(europe)[0]["team"] == "Team WRT"
    row = gtwce.parse_entry_list(america)[0]
    assert row["team"] == "AF Corse USA" and row["drivers"] == ["Matias Perez Companc", "Frederik Schandorff"]


def test_f2_f3_teams_come_from_the_driver_cards():
    tag = base64.b64encode(json.dumps({"driver_team": "Invicta Racing"}).encode()).decode()
    page = (f'<a data-uds-a7s-context="{tag}" class="x" href="/en/drivers/rafael-camara"><p>Rafael</p><p>Câmara</p></a>'
            '<a data-uds-a7s-context="!!" href="/en/drivers/no-team"><p>No</p><p>Team</p></a>'
            '<a href="/en/drivers/rafael-camara"><p>R. Câmara</p></a>')
    assert [(e.name, e.team) for e in fia_feeder.parse_drivers(page)] == [("Rafael Câmara", "Invicta Racing"), ("No Team", "")]
    assert teamnames.entries("f1a", [Entry("Alisha  Palmowski ", "Campos Racing")])[0].name == "Alisha Palmowski"
