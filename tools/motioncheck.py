"""Motion check (docs/motion.md): a phone-sized headless Chromium opens the app (fixture data, tools/perf_server.py) and
checks the motion rules that can be measured.

  opens    every page opens fresh within OPEN_MS, stays responsive (no endless loop) and throws no page error
  still    an in-page switch tapped while scrolled down moves nothing: scroll position and the anchors (heading,
           the switch) stay within STILL_PX for STILL_MS
  pill     every visible switch's pill ends up under its picked option, within PILL_PX
  morph    tapping a card plays its shared-element transition (where the browser has View Transitions), lands on
           the page it opens and leaves no view-transition-name behind

    .venv/bin/python tools/motioncheck.py              # starts tools/perf_server.py on a spare port
    .venv/bin/python tools/motioncheck.py --url http://127.0.0.1:8788    # an app you're already running

Needs Playwright, like tools/perfcheck.py (PERF_CHROMIUM points at an installed Chromium). Exits 1 when a check fails.
In GitHub Actions the table also goes to the job summary. The part below the app's settings is the same in both apps."""
from __future__ import annotations

import argparse
import os
import shutil
import sys

from perfcheck import start_server

# ---- this app ----------------------------------------------------------------------------------------------------
PAGES = ["#/", "#/wire", "#/picks", "#/standings", "#/series", "#/series/f1", "#/settings", "#/new", "#/app", "#/report",
         "#/admin", "#/search", "#/event/f1/2026-round-7", "#/driver/F1%20Driver%201"]
# (page, what to tap, anchors that must not move). Taps are plain JS clicks: a real finger can only tap what's on
# screen, and Playwright's own click would scroll the target into view first (a jump the app didn't make).
STILL = [
    ("#/standings/f1/drivers", '.series-pick a[href^="#/standings/f2/"]', ["#view h1", ".series-pick", "#view .tabs"]),
    ("#/standings/f1/drivers", '#view .tabs a[href="#/standings/f1/teams"]', ["#view h1", ".series-pick", "#view .tabs"]),
    ("#/series/f1/schedule", '#view .tabs a[href="#/series/f1/drivers"]', ["#view h1", "#view .tabs"]),
    ("#/wire", '#view .tabs [data-kind="news"]', ["#view h1", "#view .tabs"]),
    ("#/picks", ".series-pick a:not(.active)", ["#view h1", ".series-pick"]),
]
# Shared-element page changes (View Transitions): (page, card to tap, address it must land on, transition name).
MORPH = [("#/standings/f1/drivers", "#view .srow a.who-link", "#/driver/", "hero"),
         ("#/", "#view a.ev-link", "#/event/", "hero")]
SWITCHES, ON, PILL = ".series-pick, .tabs, .seg", ".active", ".seg-pill"

# ---- the checks (the same in both apps) ---------------------------------------------------------------------------
OPEN_MS, SETTLE_MS, STILL_MS, STILL_PX, PILL_PX = 8000, 3200, 800, 1, 2
VIEWPORT = {"width": 390, "height": 844}

TRACK = """anchors => ({ y: scrollY, tops: anchors.map(s => { const e = document.querySelector(s);
  return e ? e.getBoundingClientRect().top : null; }) })"""
PILLS = """([sel, on, pill]) => [...document.querySelectorAll(sel)].filter(sw => sw.offsetParent).flatMap(sw => {
  const opt = sw.querySelector(`:scope > ${on}`); if (!opt) return [];
  const p = sw.querySelector(`:scope > ${pill}`);
  const name = sw.getAttribute("aria-label") || "." + [...sw.classList].filter(c => !["has-pill", "sliding"].includes(c)).join(".");
  if (!p) return [[name, "no pill"]];
  const a = p.getBoundingClientRect(), b = opt.getBoundingClientRect();
  return [[name, Math.max(Math.abs(a.left - b.left), Math.abs(a.width - b.width))]]; })"""
# Taps the first card on screen and lists the view-transition animations that run in the next 1.2 s.
MORPHS = """async tap => {
  const a = [...document.querySelectorAll(tap)].find(a => { const r = a.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; });
  if (!a) return null;
  const seen = new Set(), t0 = performance.now();
  a.click();
  while (performance.now() - t0 < 1200) {
    for (const an of document.getAnimations()) if (an.effect?.pseudoElement) seen.add(an.effect.pseudoElement);
    await new Promise(r => requestAnimationFrame(r));
  }
  return { vt: !!document.startViewTransition, seen: [...seen] };
}"""


def check(url: str) -> list[tuple[str, str, str, bool]]:
    from playwright.sync_api import TimeoutError as PwTimeout, sync_playwright
    rows: list[tuple[str, str, str, bool]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("PERF_CHROMIUM") or None)
        ctx = browser.new_context(viewport=VIEWPORT, is_mobile=True, has_touch=True, service_workers="block")

        def fresh(hash_: str):
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e).split("\n")[0]))
            page.goto(f"{url}/{hash_}", wait_until="commit")
            page.wait_for_timeout(SETTLE_MS)   # data, the phone's idiomorph (loaded 2.5 s in) and the pills
            return page, errors

        for hash_ in PAGES:
            page, errors = fresh(hash_)
            try:
                page.wait_for_function("() => document.getElementById('view')?.firstElementChild", timeout=OPEN_MS)
                page.wait_for_function("() => true", timeout=4000)   # a page stuck in an endless loop never answers
            except PwTimeout:
                rows.append(("opens", hash_, "frozen or empty", False))
                page.close()
                continue
            rows.append(("opens", hash_, "; ".join(errors) or "ok", not errors))
            page.wait_for_timeout(500)   # a pill that just slid finishes its 380 ms
            for name, off in page.evaluate(PILLS, [SWITCHES, ON, PILL]):
                ok = off != "no pill" and off <= PILL_PX
                rows.append(("pill", f"{hash_} {name}", off if isinstance(off, str) else f"{off:.1f} px off", ok))
            page.close()

        for hash_, tap, anchors in STILL:
            page, errors = fresh(hash_)
            target = page.query_selector(tap)
            if not target:
                rows.append(("still", f"{hash_} {tap}", "nothing to tap (fixture data changed?)", False))
                page.close()
                continue
            # Scroll down so the switch sits just under the top bar: as far down as someone can be and still tap it.
            page.evaluate("""tap => { const t = document.querySelector(tap).getBoundingClientRect().top;
              scrollTo(0, Math.max(0, Math.min(scrollY + t - 120, document.documentElement.scrollHeight - innerHeight))); }""", tap)
            page.wait_for_timeout(300)
            before = page.evaluate(TRACK, anchors)
            page.evaluate("tap => document.querySelector(tap).click()", tap)
            worst = 0.0
            for _ in range(STILL_MS // 40):
                page.wait_for_timeout(40)
                now = page.evaluate(TRACK, anchors)
                moves = [abs(now["y"] - before["y"])] + [abs(a - b) for a, b in zip(now["tops"], before["tops"]) if a is not None and b is not None]
                worst = max(worst, *moves)
            ok = worst <= STILL_PX and not errors
            rows.append(("still", f"{hash_} {tap}", "; ".join(errors) or f"moved {worst:.1f} px", ok))
            page.close()

        for hash_, tap, lands, name in MORPH:
            page, errors = fresh(hash_)
            got = page.evaluate(MORPHS, tap)
            page.wait_for_timeout(300)
            landed = page.evaluate("location.hash").startswith(lands)
            left = page.evaluate("() => document.querySelectorAll('[style*=view-transition-name]').length")
            played = not got or not got["vt"] or f"::view-transition-group({name})" in got["seen"]
            ok = bool(got) and played and landed and not left and not errors
            res = ("nothing to tap" if not got else "; ".join(errors) or ("no morph" if not played else "didn't land" if not landed
                   else f"{left} names left behind" if left else "morphed" if got["vt"] else "no View Transitions: plain change"))
            rows.append(("morph", f"{hash_} {tap}", res, ok))
            page.close()
        browser.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", help="an app that's already running (default: start tools/perf_server.py)")
    args = ap.parse_args()
    proc = data = None
    url = args.url.rstrip("/") if args.url else None
    if not url:
        url, proc, data = start_server()
    try:
        rows = check(url)
    finally:
        if proc:
            proc.terminate()
            proc.wait(10)
            shutil.rmtree(data, ignore_errors=True)
    bad = [r for r in rows if not r[3]]
    lines = ["| check | where | result | |", "|---|---|---|---|"]
    lines += [f"| {k} | `{w}` | {res} | {'✅' if ok else '❌'} |" for k, w, res, ok in rows]
    report = "\n".join(lines) + f"\n\n**{f'{len(bad)} failed' if bad else 'all still'}** ({len(rows)} checks)\n"
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("## Motion\n\n" + report)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
