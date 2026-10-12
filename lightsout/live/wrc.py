"""WRC live rally timing: polls the JSON service behind wrc.com's results pages.

A rally isn't one race but ~20 timed stages over 3-4 days. What's useful to follow:
  * the overall classification (total time, gap to the leader) - who's winning the rally
  * the latest stage's times - what just happened
  * where we are: stage x of y, the next stage
Rally1 (the top WRC class) and Rally2 (WRC2) crews run on the same stages, so each row
carries its class and the app can filter.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from ..adapters.base import get_json, tidy_caps
from ..adapters.wrc import API, ms_to_time, person, season_id
from .events import EventLog

log = logging.getLogger("lightsout.live.wrc")
POLL = 30  # seconds; stage times arrive as each car reaches the finish


class WrcState:
    def __init__(self):
        self.series = "wrc"
        self.raw: dict = {}          # non-empty once data arrives
        self.event: dict = {}
        self.entries: dict[int, dict] = {}
        self.stages: list[dict] = []
        self.overall: list[dict] = []
        self.stage_times: list[dict] = []
        self.stage: dict | None = None
        self.changed = time.time()
        self.log = EventLog("wrc")
        self._done: set = set()
        self._leader = None
        self._penalty: dict = {}

    def _crew(self, entry_id: int) -> dict:
        e = self.entries.get(entry_id, {})
        d = e.get("driver") or {}
        return {"number": e.get("identifier", ""), "code": d.get("code", ""), "name": person(d),
                "codriver": person(e.get("codriver")), "team": tidy_caps((e.get("entrant") or e.get("manufacturer") or {}).get("name", "")),
                "class": (e.get("group") or {}).get("name", ""), "car": e.get("vehicleModel", "")}

    def snapshot(self) -> dict:
        done = [s for s in self.stages if s.get("status") == "Completed"]
        nxt = next((s for s in self.stages if s.get("status") not in ("Completed", "Cancelled")), None)
        rows = [{**self._crew(r["entryId"]), "pos": r.get("position") or 99,
                 "total": ms_to_time(r.get("totalTimeMs")), "gap": ms_to_time(r.get("diffFirstMs"), plus=True) if r.get("diffFirstMs") else "",
                 "interval": ms_to_time(r.get("diffPrevMs"), plus=True) if r.get("diffPrevMs") else "",
                 "penalty": ms_to_time(r.get("penaltyTimeMs")) if r.get("penaltyTimeMs") else ""}
                for r in self.overall]
        stage_rows = [{**self._crew(t["entryId"]), "pos": t.get("position") or 99,
                       "time": ms_to_time(t.get("elapsedDurationMs")),
                       "gap": ms_to_time(t.get("diffFirstMs"), plus=True) if t.get("diffFirstMs") else ""}
                      for t in self.stage_times if t.get("status") == "Completed"]
        st = self.stage or {}
        return {
            "series": "wrc", "kind": "rally",
            "event": self.event.get("name", ""), "session": f"{st.get('code', '')} {st.get('name', '')}".strip(),
            "status": st.get("status", ""), "track": "",
            "lap": len(done), "total_laps": len(self.stages),
            "stage": {"code": st.get("code"), "name": st.get("name"), "km": st.get("distance"), "status": st.get("status")},
            "next": {"code": nxt.get("code"), "name": nxt.get("name"), "km": nxt.get("distance")} if nxt and nxt is not self.stage else None,
            "rows": sorted(rows, key=lambda r: r["pos"]),
            "stage_rows": sorted(stage_rows, key=lambda r: r["pos"]),
            "messages": [], "updated": self.changed,
        }


async def current_event_id(now: datetime) -> int | None:
    if os.environ.get("LIGHTSOUT_WRC_EVENT"):  # testing: follow a past rally
        return int(os.environ["LIGHTSOUT_WRC_EVENT"])
    sid = await season_id(now.year)
    if not sid:
        return None
    detail = await get_json(f"{API}/season-detail.json?seasonId={sid}")
    lo, hi = (now - timedelta(days=1)).date().isoformat(), (now + timedelta(days=1)).date().isoformat()
    for r in detail.get("seasonRounds") or []:
        ev = r.get("event") or {}
        if ev.get("startDate", "9") <= hi and ev.get("finishDate", "0") >= lo:
            return ev["eventId"]
    return None


async def poll_once(state: WrcState, event_id: int) -> None:
    ev = await get_json(f"{API}/events/{event_id}.json")
    rally = next((r for r in ev.get("rallies") or [] if r.get("isMain")), (ev.get("rallies") or [{}])[0])
    rid = rally["rallyId"]
    if state.event.get("eventId") != event_id or not state.entries:
        state.entries = {e["entryId"]: e for e in await get_json(f"{API}/events/{event_id}/rallies/{rid}/entries.json")}
    state.event = ev
    itinerary = await get_json(f"{API}/events/{event_id}/itineraries/{rally['itineraryId']}.json")
    state.stages = [s for leg in itinerary.get("itineraryLegs") or [] for sec in leg.get("itinerarySections") or []
                    for s in sec.get("stages") or []]
    state.overall = await get_json(f"{API}/events/{event_id}/rallies/{rid}/results.json")
    # The stage to show: one in progress (has some times but isn't completed), else the last completed.
    running = [s for s in state.stages if s.get("status") in ("Running", "Interrupted", "Started")]
    done = [s for s in state.stages if s.get("status") == "Completed"]
    state.stage = (running or done[-1:] or state.stages[:1] or [None])[-1 if not running else 0]
    if state.stage:
        state.stage_times = await get_json(f"{API}/events/{event_id}/stages/{state.stage['stageId']}/stagetimes.json?rallyId={rid}")
    state.raw = {"event": event_id}
    state.changed = time.time()
    try:
        await rally_events(state, event_id, rid)
    except Exception:
        log.exception("WRC event extraction failed")


async def rally_events(state: WrcState, event_id: int, rid: int) -> None:
    """Stage winners, overall lead changes and penalties, from the change since the last poll."""
    done = {s["stageId"] for s in state.stages if s.get("status") == "Completed"}
    leader = next((r["entryId"] for r in state.overall if r.get("position") == 1), None)
    penalty = {r["entryId"]: r.get("penaltyTimeMs") or 0 for r in state.overall}
    label = state.event.get("name", "")
    if not state.log.primed:
        state._done, state._leader, state._penalty, state.log.primed = done, leader, penalty, bool(state.stages)
        return
    for st in [s for s in state.stages if s["stageId"] in done - state._done]:
        times = await get_json(f"{API}/events/{event_id}/stages/{st['stageId']}/stagetimes.json?rallyId={rid}")
        best = next((t for t in times if t.get("position") == 1), None)
        if best:
            c = state._crew(best["entryId"])
            state.log.emit(f"{event_id}:stage:{st['stageId']}", "stage", f"{st.get('code')} {st.get('name')}: fastest {c['name']}",
                           numbers=[c["number"]], names=[c["name"]], session=label, important=False)
    after = next((s.get("code") for s in reversed(state.stages) if s["stageId"] in done), "")
    if leader and leader != state._leader:
        c = state._crew(leader)
        state.log.emit(f"{event_id}:lead:{leader}:{after}", "lead", f"{c['name']} leads the rally after {after}",
                       numbers=[c["number"]], names=[c["name"]], session=label)
    for entry, ms in penalty.items():
        if ms > state._penalty.get(entry, 0):
            c = state._crew(entry)
            state.log.emit(f"{event_id}:pen:{entry}:{ms}", "penalty", f"{c['name']}: penalty, now {ms_to_time(ms)} in total",
                           numbers=[c["number"]], names=[c["name"]], session=label)
    state._done, state._leader, state._penalty = done | state._done, leader or state._leader, penalty


async def run(state: WrcState, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            event_id = await current_event_id(datetime.now(timezone.utc))
            if event_id:
                await poll_once(state, event_id)
        except Exception as exc:
            log.warning("WRC live poll failed: %r", exc)
        try:
            await asyncio.wait_for(stop.wait(), POLL)
        except asyncio.TimeoutError:
            pass
