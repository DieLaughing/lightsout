"""Race events: what happened during a session, as short tagged lines (incidents, penalties, cautions...).

Each live state keeps an EventLog. Events come from two places:
  * written messages: F1/F2/F3 race control, NASCAR lap notes, caution reasons
  * changes between two feed updates: a car retires, the lead changes, a flag changes
A change seen on the very first update after connecting isn't news (we just arrived), so states call
`baseline()` until they've seen one full update.

kind: start, caution, red_flag, restart, incident, investigation, penalty, track_limits, retired, lead,
      pit, stage, result, note, info. `important` events go to the Wire and alerts; the rest stay on the
      event's Race story.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

IMPORTANT = {"start", "caution", "red_flag", "investigation", "penalty", "retired", "lead", "stage", "result", "incident"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_hash(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


class EventLog:
    def __init__(self, series: str):
        self.series = series
        self._out: list[dict] = []
        self._seen: set[str] = set()
        self.primed = False

    def emit(self, key: str, kind: str, text: str, *, lap=None, numbers=(), names=(), time: str | None = None,
             important: bool | None = None, session: str = "") -> None:
        """Queue an event. `key` must be stable for the same real-world event (reconnects resend history)."""
        eid = f"{self.series}:{key}"
        if eid in self._seen:
            return
        self._seen.add(eid)
        self._out.append({"id": eid, "series": self.series, "kind": kind, "text": text, "lap": lap,
                          "numbers": [str(n) for n in numbers], "names": list(names), "time": time or now_iso(),
                          "important": kind in IMPORTANT if important is None else important, "session": session})

    def take(self) -> list[dict]:
        out, self._out = self._out, []
        return out


# ---- race control text (F1, F2, F3, F1 Academy) -----------------------------------------------
NOISE = re.compile(r"\b(DRS (ENABLED|DISABLED)|BLUE FLAG|CLEAR IN TRACK SECTOR|YELLOW IN TRACK SECTOR|"
                   r"DOUBLE YELLOW IN TRACK SECTOR|GREEN LIGHT - PIT EXIT|PIT EXIT CLOSED|PIT ENTRY CLOSED|"
                   r"PIT EXIT OPEN|RISK OF RAIN|TRACK SURFACE SLIPPERY|LAPPED CARS)\b")


def classify(text: str, category: str = "", flag: str = "") -> str | None:
    """Kind of a race control message, or None for routine noise (sector flags, DRS, blue flags)."""
    t = (text or "").upper()
    if not t or NOISE.search(t):
        return None
    if flag == "CHEQUERED" or "CHEQUERED FLAG" in t:
        return None  # the result event covers the finish, with the winner
    if flag == "RED" or re.search(r"\bRED FLAG\b", t) or "SESSION SUSPENDED" in t:
        return "red_flag"
    if category == "SafetyCar" or "SAFETY CAR" in t:
        return "caution" if "DEPLOYED" in t else "restart" if ("IN THIS LAP" in t or "ENDING" in t) else "info"
    if "PENALTY" in t or "DRIVE THROUGH" in t or "STOP/GO" in t or "STOP AND GO" in t or "DISQUALIFIED" in t:
        return "investigation" if "NOTED" in t and "PENALTY" not in t.split("NOTED")[-1] else "penalty"
    if "UNDER INVESTIGATION" in t or "WILL BE INVESTIGATED" in t:
        return "investigation"
    if "NO FURTHER" in t or "REVIEWED" in t:
        return "info"
    if "DELETED" in t or "TRACK LIMITS" in t:
        return "track_limits"
    if "INCIDENT" in t or "COLLISION" in t or "CAUSING" in t:
        return "incident"
    if "RETIRED" in t or "STOPPED" in t:
        return "retired"
    if flag in ("GREEN", "CLEAR", "YELLOW", "DOUBLE YELLOW"):
        return None
    if flag == "BLACK AND WHITE":
        return "info"
    return "info"


CARS = re.compile(r"\bCARS? ((?:\d{1,3}(?: ?\([A-Z]{3}\))?(?:, | AND |,| & )?)+)")


def car_numbers(text: str) -> list[str]:
    """'CARS 4 (NOR) AND 81 (PIA)' -> ['4', '81']."""
    out = []
    for m in CARS.finditer((text or "").upper()):
        out += re.findall(r"\b\d{1,3}\b", m.group(1))
    return list(dict.fromkeys(out))


def tidy(text: str) -> str:
    """Race control shouts in capitals; make it readable without losing team and driver codes."""
    t = (text or "").strip()
    if t.upper() != t:
        return t
    s = t.capitalize()
    s = re.sub(r"\(([a-z]{3})\)", lambda m: f"({m.group(1).upper()})", s)
    for w in ("Fia", "Drs", "Vsc", "Sc", "Fp1", "Fp2", "Fp3"):
        s = re.sub(rf"\b{w}\b", w.upper(), s, flags=re.IGNORECASE)
    return s
