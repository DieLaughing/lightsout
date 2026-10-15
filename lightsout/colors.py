"""Team colours from data/colors.yaml: the longest name fragment found in a team name wins."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from .series import BY_ID

DATA = Path(__file__).resolve().parent / "data" / "colors.yaml"


@lru_cache(maxsize=1)
def _table() -> list[tuple[re.Pattern, str]]:
    """Whole words only, so "ART" doesn't paint every Aston Martin."""
    pairs = sorted((yaml.safe_load(DATA.read_text()) or {}).items(), key=lambda kv: -len(kv[0]))
    return [(re.compile(rf"(?<![\w-]){re.escape(k)}(?![\w-])", re.I), v) for k, v in pairs]


@lru_cache(maxsize=2048)
def for_team(team: str, series: str = "") -> str:
    """The team's colour, else the series colour (else "")."""
    hit = next((c for k, c in _table() if k.search(team)), None) if team else None
    return hit or (BY_ID[series].color if series in BY_ID else "")


def tint(table: dict, series: str) -> dict:
    """A copy of a standings table with `color` on every row (drivers, teams, classes, last race)."""
    out = dict(table)
    out["drivers"] = [{**r, "color": for_team(r.get("team", ""), series)} for r in table.get("drivers") or []]
    out["teams"] = [{**r, "color": for_team(r.get("name", ""), series)} for r in table.get("teams") or []]
    out["last_results"] = [{**r, "color": for_team(r.get("team", ""), series)} for r in table.get("last_results") or []]
    out["classes"] = {k: [{**r, "color": for_team(r.get("team", ""), series)} for r in v]
                      for k, v in (table.get("classes") or {}).items()}
    return out
