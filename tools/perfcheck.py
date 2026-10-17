"""Speed budget check (docs/operating.md): a phone-sized headless Chromium with 200 ms added to every request (the
"slow relay") opens the app, visits every tab and an event, driver and series page, then comes back to each one. Every API call the
pages made is then timed directly against the server.

  first visit    content on screen after opening the app with nothing cached          < FIRST_MS
  revisit        content on screen after going back to a page already seen            < REVISIT_MS
  api p95        95th percentile of RUNS direct requests to each API path the pages use < API_P95_MS

    .venv/bin/python tools/perfcheck.py              # starts tools/perf_server.py (fixture data) on a spare port
    .venv/bin/python tools/perfcheck.py --url http://127.0.0.1:8787    # an app you're already running

Needs Playwright (`.venv/bin/pip install playwright`, then `.venv/bin/playwright install chromium`, or point
PERF_CHROMIUM at an installed Chromium). Exits 1 when anything is over budget. In GitHub Actions the table also goes to
the job summary."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRST_MS, REVISIT_MS, API_P95_MS = 1500, 300, 200
LATENCY_MS = 200          # added to every request: the slow relay
DOWN_KBPS = 1600          # a slow phone connection (~"slow 4G")
RUNS = 20
TABS = ["#/wire", "#/picks", "#/standings", "#/series", "#/settings", "#/"]
DETAIL = ['a[href^="#/event/"]', 'a[href^="#/driver/"]', 'a[href^="#/series/"]']   # found on the pages: the first link of each

# What #view shows: 0 = nothing new yet (or the "Loading…" placeholder), 1 = the page, 2 = an error message. A page
# that fails fast must not pass as a fast page, so errors come back as -1 (reported "error", over budget).
STATE = """old => { const v = document.getElementById("view"), f = v && v.firstElementChild;
  if (!f || f === old || (f.classList.contains("empty") && /^Loading/.test(f.textContent))) return 0;
  return v.querySelector(".error") || /Couldn['’]t (load|reach)/.test(v.textContent) ? 2 : 1; }"""
GO = """async ([hash, wait]) => {
  const state = %s, old = document.getElementById("view").firstElementChild, t0 = performance.now();
  if (location.hash === hash) return 0;   // already there: no navigation to wait for
  location.hash = hash;
  return await new Promise(done => {
    const check = () => { const s = state(old), ms = performance.now() - t0;
      s ? done(s === 2 ? -1 : ms) : ms > wait ? done(null) : requestAnimationFrame(check); };
    requestAnimationFrame(check);
  });
}""" % STATE
FIRST = """wait => new Promise(done => {
  const check = () => { const s = document.getElementById("view") ? (%s)(null) : 0, ms = performance.now();
    s ? done(s === 2 ? -1 : ms) : ms > wait ? done(null) : requestAnimationFrame(check); };
  check();
})""" % STATE


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server() -> tuple[str, subprocess.Popen, str]:
    port, data = free_port(), tempfile.mkdtemp(prefix="lightsout-perf-")
    proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "perf_server.py"), "--port", str(port), "--data", data])
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{url}/healthz", timeout=1)
            return url, proc, data
        except OSError:
            time.sleep(0.1)
    proc.kill()
    sys.exit("perf server didn't start")


def browse(url: str) -> tuple[float, dict[str, float | None], set[str]]:
    from playwright.sync_api import sync_playwright
    apis: set[str] = set()
    with sync_playwright() as pw:
        exe = os.environ.get("PERF_CHROMIUM") or None
        browser = pw.chromium.launch(executable_path=exe)
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                  service_workers="block")   # a first visit has no service worker yet
        page = ctx.new_page()
        page.on("request", lambda r: apis.add(r.url[len(url):]) if r.method == "GET" and r.url.startswith(f"{url}/api/") else None)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": LATENCY_MS,
                                                      "downloadThroughput": DOWN_KBPS * 125, "uploadThroughput": DOWN_KBPS * 125})
        page.goto(url + "/", wait_until="commit")
        first = page.evaluate(FIRST, 15000)
        page.wait_for_timeout(4000)                  # prefetch() runs 2.5 s after start
        pages = list(TABS)
        for sel in DETAIL:                           # detail pages, from links the app shows
            for hop in ["#/", "#/standings", "#/series"]:
                page.evaluate(GO, [hop, 10000])
                href = page.eval_on_selector(sel, "a => a.getAttribute('href')") if page.query_selector(sel) else None
                if href:
                    pages.append(href)
                    break
        for hash in pages:                           # see each page once, then come back to it
            page.evaluate(GO, [hash, 10000])
            page.evaluate(GO, ["#/new", 10000])
        revisit = {}
        for hash in pages:
            page.evaluate(GO, ["#/new" if hash != "#/new" else "#/", 10000])
            revisit[hash] = page.evaluate(GO, [hash, 5000])
        browser.close()
    return first, revisit, apis


def time_apis(url: str, paths: set[str]) -> dict[str, float]:
    out = {}
    for path in sorted(paths):
        took = []
        for _ in range(RUNS):
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(url + path, timeout=10) as r:
                    r.read()
            except urllib.error.HTTPError as e:   # pages ask for some things that may not exist (a 404 is an answer)
                e.read()
            took.append((time.perf_counter() - t0) * 1000)
        took.sort()
        out[path] = took[max(0, int(len(took) * 0.95) - 1)]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", help="an app that's already running (default: start tools/perf_server.py)")
    ap.add_argument("--json", help="also write the results here")
    args = ap.parse_args()
    proc = data = None
    url = args.url.rstrip("/") if args.url else None
    if not url:
        url, proc, data = start_server()
    try:
        first, revisit, apis = browse(url)
        api = time_apis(url, apis)
    finally:
        if proc:
            proc.terminate()
            proc.wait(10)
            shutil.rmtree(data, ignore_errors=True)

    rows = [("first visit", "/", first, FIRST_MS)]
    rows += [("revisit", h, ms, REVISIT_MS) for h, ms in revisit.items()]
    rows += [("api p95", p, ms, API_P95_MS) for p, ms in api.items()]
    over = lambda ms, b: ms is None or ms < 0 or ms > b  # noqa: E731
    bad = [r for r in rows if over(r[2], r[3])]
    shown = lambda ms: "timeout" if ms is None else "error" if ms < 0 else round(ms)  # noqa: E731
    lines = ["| check | page | ms | budget | |", "|---|---|---:|---:|---|"]
    lines += [f"| {k} | `{p}` | {shown(ms)} | {b} | {'❌' if over(ms, b) else '✅'} |" for k, p, ms, b in rows]
    verdict = f"{len(bad)} over budget" if bad else "all within budget"
    report = "\n".join(lines) + f"\n\n**{verdict}** (slow relay {LATENCY_MS} ms per request, {DOWN_KBPS} kbps)\n"
    print(report)
    if args.json:
        Path(args.json).write_text(json.dumps({"ok": not bad, "rows": rows}, indent=1))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("## Speed budget\n\n" + report)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
