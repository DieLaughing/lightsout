"""Which drivers and teams a piece of text is about. Used for news stories and race events.

Drivers come from the grids Lights Out already imports (the entry table). A full name always matches;
a surname alone matches only when exactly one person in the relevant series has it, and it isn't also
an everyday word. Teams come from data/teams.yaml (grids say "RB F1 Team", stories say "Racing Bulls").
A story about a driver also counts for their team.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

TEAMS_FILE = Path(__file__).resolve().parent / "data" / "teams.yaml"
GROUP = {"cup": "nascar", "noaps": "nascar", "truck": "nascar"}   # series sharing one team list
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
# Surnames that are also ordinary words: "power unit", "a late block". These need the full name.
COMMON = {"Bell", "Hill", "Love", "Power", "Young", "Price", "Gray", "Wood", "Block", "Rose", "Day", "Little",
          "Brown", "White", "Green", "Black", "King", "Hunt", "Ware", "Dye", "Rice", "Lane", "Hand", "Cook",
          "Moss", "Mills", "Small", "Strong", "Long", "Major", "Page", "Burton", "Jones", "Smith", "Evans", "Daly"}


def fold(s: str) -> str:
    """Strip accents, keep case: 'Pérez' -> 'Perez'."""
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode()


def surname(name: str) -> str:
    parts = [p for p in name.replace("(C)", "").split() if p]
    while len(parts) > 1 and parts[-1].lower() in SUFFIXES:
        parts.pop()
    return parts[-1] if parts else ""


def _word(s: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(fold(s)) + r"(?![\w-])")


@lru_cache(maxsize=1)
def team_config() -> dict:
    return yaml.safe_load(TEAMS_FILE.read_text())


class Tagger:
    def __init__(self, entries: list[dict]):
        """entries: rows of the entry table (series, name, team, number)."""
        cfg = team_config()
        self.people: dict[str, set[str]] = {}             # driver name -> series they're in
        self.team_of: dict[tuple[str, str], str] = {}     # (series, driver) -> canonical team
        self.by_number: dict[tuple[str, str], str] = {}   # (series, car number) -> driver
        grid_to_team = {}
        for group, teams in cfg.items():
            for team, info in teams.items():
                for g in [team, *(info.get("grid") or [])]:
                    grid_to_team[(group, g)] = team
        for e in entries:
            name, series = e["name"], e["series"]
            self.people.setdefault(name, set()).add(series)
            team = grid_to_team.get((GROUP.get(series, series), e.get("team") or ""))
            if team:
                self.team_of[(series, name)] = team
            if e.get("number"):
                self.by_number[(series, str(e["number"]))] = name
        self.full = {n: _word(n) for n in self.people}
        self.last = {n: surname(n) for n in self.people}
        self.last_re = {n: _word(s) for n, s in self.last.items() if len(s) >= 4 and s not in COMMON}
        self.teams = []  # (group, canonical team, [patterns])
        for group, teams in cfg.items():
            for team, info in teams.items():
                self.teams.append((group, team, [_word(a) for a in {team, *(info.get("aliases") or [])}]))

    def tag(self, text: str, series: list[str] | None = None) -> tuple[list[str], list[str]]:
        """(drivers, teams) mentioned in text. `series` narrows the search when the source is single-series."""
        t = fold(text)
        scope = set(series or [])
        candidates = [n for n, s in self.people.items() if not scope or s & scope]
        drivers = {n for n in candidates if self.full[n].search(t)}
        # Surname alone: only if no other candidate shares it.
        counts: dict[str, int] = {}
        for n in candidates:
            counts[self.last[n]] = counts.get(self.last[n], 0) + 1
        for n in candidates:
            rx = self.last_re.get(n)
            if rx and counts[self.last[n]] == 1 and rx.search(t):
                drivers.add(n)
        # A mixed-feed story: judge team names by the series of the drivers it mentions ("Penske" in a
        # NASCAR story is Team Penske, not Formula E's DS Penske).
        groups = {GROUP.get(s, s) for s in (scope or {x for d in drivers for x in self.people[d]})}
        teams = {team for group, team, pats in self.teams
                 if (not groups or group in groups) and any(p.search(t) for p in pats)}
        for n in drivers:
            for s in self.people[n] & (scope or self.people[n]):
                if (s, n) in self.team_of:
                    teams.add(self.team_of[(s, n)])
        return sorted(drivers), sorted(teams)

    def people_series(self) -> set[str]:
        return {s for ss in self.people.values() for s in ss}

    def driver_for_number(self, series: str, number: str) -> str | None:
        return self.by_number.get((series, str(number).lstrip("#")))

    def team_for(self, series: str, driver: str) -> str | None:
        return self.team_of.get((series, driver))

    def directory(self) -> dict:
        """Every driver and team, for the Wire's filter pickers."""
        teams: dict[str, set[str]] = {}
        for group, team, _ in self.teams:
            members = [s for s in ("cup", "noaps", "truck")] if group == "nascar" else [group]
            teams.setdefault(team, set()).update(members)
        return {"drivers": sorted(({"name": n, "series": sorted(s)} for n, s in self.people.items()), key=lambda d: d["name"]),
                "teams": sorted(({"name": t, "series": sorted(s)} for t, s in teams.items()), key=lambda d: d["name"])}
