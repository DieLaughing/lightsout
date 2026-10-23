#!/usr/bin/env python3
"""Betting odds for Lights Out, fetched from a home connection and handed to the app.

DraftKings' sportsbook serves its odds as JSON, but its CDN (Akamai) refuses data-centre addresses, so the app's
server can't ask itself. This runs somewhere with an ordinary home connection (a Raspberry Pi on a systemd timer),
writes a small odds.json and copies it to the server with rsync over SSH. The app only ever reads that file.

Each run is a handful of requests (the motorsport menu + one per league), about what one person browsing makes.
Every run appends one line to runs.jsonl (requests, status, timings, bytes, CPU, memory, upload) so it can be watched;
`--report [days]` summarises it. After FAIL_ALERT failed runs in a row it sends one ntfy message (optional).

Needs: python3 + curl_cffi (pip install curl_cffi). Settings (env or an env file passed with --env):
  ODDS_DIR      where odds.json and runs.jsonl live (default ~/odds)
  ODDS_DEST     rsync destinations, space-separated (user@host:path/); empty = keep the file local
  ODDS_SSH_KEY  key for those destinations
  NTFY_URL / NTFY_TOKEN   e.g. http://127.0.0.1:8090/lightsout and a write token
"""
from __future__ import annotations

import json
import os
import re
import resource
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

NAV = "https://sportsbook-nash.draftkings.com/api/sportscontent/navigation/dkusnj/v2/nav/sports/14?format=json"
LEAGUE = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusnj/v1/leagues/{}"
# DraftKings league name -> Lights Out series (race leagues only; the "... Championship" futures are skipped).
SERIES = [(re.compile(r"^Formula 1$"), "f1"), (re.compile(r"^NASCAR Cup Series$"), "cup"),
          (re.compile(r"^NASCAR O'Reilly"), "noaps"), (re.compile(r"^NASCAR Truck"), "truck"),
          (re.compile(r"IndyCar", re.I), "indycar")]
FAIL_ALERT = 3


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env(path: str | None) -> None:
    if path and Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def series_for(league: str) -> str | None:
    return next((s for rx, s in SERIES if rx.search(league) and "Championship" not in league), None)


def parse_league(data: dict, series: str) -> list[dict]:
    """Every event with its markets; each selection = driver, American odds, implied chance (%, bookmaker margin in)."""
    markets: dict[str, dict] = {}
    for m in data.get("markets") or []:
        markets[m["id"]] = {"event": m["eventId"], "name": m["name"], "rows": []}
    for s in data.get("selections") or []:
        m = markets.get(s.get("marketId"))
        if not m or not s.get("trueOdds"):
            continue
        american = (s.get("displayOdds") or {}).get("american", "").replace("−", "-")
        m["rows"].append({"name": s.get("label", ""), "american": american, "pct": round(100 / s["trueOdds"], 1)})
    events = []
    for e in data.get("events") or []:
        ms = [{"name": m["name"], "rows": sorted(m["rows"], key=lambda r: -r["pct"])}
              for m in markets.values() if m["event"] == e["id"] and m["rows"]]
        if ms:
            events.append({"series": series, "name": e.get("name", ""), "start": e.get("startEventDate", "")[:19] + "Z",
                           "markets": ms})
    return events


def fetch(log: dict) -> list[dict]:
    from curl_cffi import requests

    def get(url: str) -> dict:
        t = time.monotonic()
        try:
            r = requests.get(url, impersonate="chrome", timeout=25)
        except Exception as exc:
            log["requests"].append({"url": url.rsplit("/", 1)[-1][:40], "error": f"{type(exc).__name__}: {exc}"[:160],
                                    "ms": round((time.monotonic() - t) * 1000)})
            raise
        log["requests"].append({"url": url.rsplit("/", 1)[-1][:40], "status": r.status_code, "bytes": len(r.content),
                                "ms": round((time.monotonic() - t) * 1000)})
        r.raise_for_status()
        return r.json()

    events = []
    for league in get(NAV).get("leagues") or []:
        series = series_for(league.get("name", ""))
        if series:
            time.sleep(1)  # one request at a time, a second apart
            events += parse_league(get(LEAGUE.format(league["id"])), series)
    return events


def upload(path: Path, log: dict) -> None:
    dests, key = os.environ.get("ODDS_DEST", "").split(), os.environ.get("ODDS_SSH_KEY", "")
    for dest in dests:
        t = time.monotonic()
        ssh = f"ssh -o BatchMode=yes -o ConnectTimeout=15{f' -i {key}' if key else ''}"
        r = subprocess.run(["rsync", "-t", "-e", ssh, str(path), dest], capture_output=True, text=True, timeout=90)
        log["uploads"].append({"dest": dest.split(":")[0], "ok": r.returncode == 0, "ms": round((time.monotonic() - t) * 1000),
                               **({"error": r.stderr.strip()[-200:]} if r.returncode else {})})


def notify(text: str) -> None:
    url, token = os.environ.get("NTFY_URL"), os.environ.get("NTFY_TOKEN")
    if not url:
        return
    req = urllib.request.Request(url, data=text.encode(), headers={"Title": "Lights Out odds", "Tags": "warning",
                                                                   **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as exc:
        print(f"ntfy failed: {exc!r}", file=sys.stderr)


def run(base: Path) -> int:
    started, wall = now(), time.monotonic()
    log = {"time": started, "requests": [], "uploads": []}
    try:
        events = fetch(log)
        out = base / "odds.json"
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps({"source": "DraftKings", "fetched": started, "events": events}, separators=(",", ":")))
        tmp.replace(out)
        log.update(ok=True, events=len(events), selections=sum(len(m["rows"]) for e in events for m in e["markets"]),
                   file_bytes=out.stat().st_size)
        upload(out, log)
        if any(not u["ok"] for u in log["uploads"]):
            log["ok"] = False
            log["error"] = "upload failed"
    except Exception as exc:
        log.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
    usage = resource.getrusage(resource.RUSAGE_SELF)
    log.update(wall_s=round(time.monotonic() - wall, 2), cpu_s=round(usage.ru_utime + usage.ru_stime, 2),
               max_rss_mb=round(usage.ru_maxrss / 1024, 1))
    with (base / "runs.jsonl").open("a") as f:
        f.write(json.dumps(log) + "\n")
    print(json.dumps(log))
    if not log["ok"]:
        streak = 0
        for line in reversed((base / "runs.jsonl").read_text().splitlines()):
            if json.loads(line).get("ok"):
                break
            streak += 1
        if streak == FAIL_ALERT:  # once per streak
            notify(f"Odds fetch failed {streak} times in a row. Last error: {log.get('error')}")
    return 0 if log["ok"] else 1


def report(base: Path, days: float) -> None:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = [json.loads(x) for x in (base / "runs.jsonl").read_text().splitlines() if x.strip()]
    runs = [r for r in runs if r["time"] >= since]
    if not runs:
        print("no runs")
        return
    ok = [r for r in runs if r.get("ok")]
    reqs = [q for r in runs for q in r["requests"]]
    codes: dict[str, int] = {}
    for q in reqs:
        k = str(q.get("status") or q.get("error", "error").split(":")[0])
        codes[k] = codes.get(k, 0) + 1
    ms = sorted(q["ms"] for q in reqs)
    print(f"runs: {len(runs)} since {since} ({len(ok)} ok, {len(runs) - len(ok)} failed)")
    print(f"requests: {len(reqs)}  by status: {codes}  median {ms[len(ms) // 2]} ms, max {ms[-1]} ms")
    print(f"downloaded: {sum(q.get('bytes', 0) for q in reqs) / 1e6:.1f} MB  odds file: {ok[-1].get('file_bytes', 0) / 1e3:.0f} KB" if ok else "")
    print(f"per run: wall {max(r['wall_s'] for r in runs)} s max, cpu {max(r['cpu_s'] for r in runs)} s max, "
          f"memory {max(r['max_rss_mb'] for r in runs)} MB max")
    ups = [u for r in runs for u in r["uploads"]]
    print(f"uploads: {sum(u['ok'] for u in ups)}/{len(ups)} ok")
    for r in [r for r in runs if not r.get("ok")][-5:]:
        print(f"  failed {r['time']}: {r.get('error')}")
    if ok:
        print(f"last good run {ok[-1]['time']}: {ok[-1].get('events')} events, {ok[-1].get('selections')} odds")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--env" in args:
        load_env(args[args.index("--env") + 1])
    base = Path(os.environ.get("ODDS_DIR") or Path.home() / "odds")
    base.mkdir(parents=True, exist_ok=True)
    if "--report" in args:
        i = args.index("--report")
        report(base, float(args[i + 1]) if len(args) > i + 1 and args[i + 1][0].isdigit() else 7)
    else:
        sys.exit(run(base))
