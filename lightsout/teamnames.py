"""One spelling per team, as fans know it, in every list the app shows (grids, standings, last race).

Feeds disagree with themselves: Jolpica writes "RB F1 Team", Formula E's grid shouts "JAGUAR TCS RACING" while its
standings say "Jaguar TCS Racing", MotoGP's rider list adds trailing spaces. A name in data/teams.yaml (any case) and
its `spell:` list show as that name. `grid:` is NOT used here: it is for tagging news and deliberately merges
separate entries (Toyota's WRT and WRT2). Anything not listed is kept, minus stray whitespace. "Rally1 · <team>" and
"#24 · <team>" are tidied part by part. Applied when a refresh saves, so pages never do this work.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .models import Entry, Standings
from .names import GROUP, team_config

NASCAR = {"cup", "noaps", "truck"}
_CAR = re.compile(r"^#(\w+) · (.+)$")


def _clean(s: str) -> str:
    return " ".join((s or "").split())


@lru_cache(maxsize=1)
def _names() -> dict[tuple[str, str], str]:
    out = {}
    for group, teams in team_config().items():
        for team, info in (teams or {}).items():
            for spelling in [team, *((info or {}).get("spell") or [])]:
                out[(group, _clean(spelling).casefold())] = team
    return out


def tidy(series: str, name: str) -> str:
    group, known = GROUP.get(series, series), _names()
    return " · ".join(known.get((group, p.casefold()), p) for p in _clean(name).split(" · "))


def entries(series: str, rows: list[Entry]) -> list[Entry]:
    for e in rows:
        e.name, e.team = _clean(e.name), tidy(series, e.team)  # F1 Academy's grid has "Alisha  Palmowski"
    return rows


def standings(series: str, st: Standings, grid: dict[str, str] | None = None) -> Standings:
    """grid: driver -> team from the series' grid. NASCAR's points feed and grid name only the manufacturer ("#24 ·
    Chevrolet"); the latest official results ("#24 · Hendrick Motorsports") know the team, so rows become
    "#24 · Hendrick Motorsports · Chevrolet". A driver missing from those results keeps the make."""
    for r in st.last_results:
        r.team = tidy(series, r.team)
    known = {**(grid or {}), **{r.name: r.team for r in st.last_results if r.team}} if series in NASCAR else {}
    for r in st.drivers:
        r.team = tidy(series, r.team)
        if (car := _CAR.match(r.team)) and (team := _CAR.match(known.get(r.name, ""))) and team[2] != car[2]:
            r.team = f"#{car[1]} · {team[2]} · {car[2]}"
    for r in st.teams:
        r.name = tidy(series, r.name)
    for rows in st.classes.values():
        for r in rows:
            r.team = tidy(series, r.team)
    return st
