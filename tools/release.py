"""Publish one GitHub Release per day of updates in lightsout/data/changes.yaml (tag v<date>, e.g. v2026.10.12).

Past days are tagged at that day's last commit; today's at HEAD. A release that already exists gets its notes
refreshed if entries were added; today's also moves to HEAD and rebuilds the Docker image. Needs the gh CLI signed in. Run after pushing: .venv/bin/python tools/release.py

The image only builds if the speed budget passes (tools/perfcheck.py runs first in .github/workflows/docker.yml).
To ship over budget on purpose: tools/release.py --perf-override "why", which puts the reason in today's notes.
"""
import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lightsout import changes  # noqa: E402


def run(*args, check=True) -> str:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=check).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perf-override", metavar="WHY", help="build today's image even if the speed budget fails")
    why = ap.parse_args().perf_override
    today = date.today().isoformat()
    for day, items in sorted(changes.by_date(changes.load()).items()):
        tag = "v" + day.replace("-", ".")
        notes = changes.day_notes(items) + "\n\nFull history: [CHANGELOG.md](CHANGELOG.md)"
        if why and day == today:
            notes += f"\n\nperf-override: {why}"
        exists = subprocess.run(["gh", "release", "view", tag], cwd=ROOT, capture_output=True).returncode == 0
        if exists:
            current = run("gh", "release", "view", tag, "--json", "body", "--jq", ".body")
            kept = [line for line in current.splitlines() if line.startswith("perf-override:")]
            if kept and not (why and day == today):   # an override stays on the record when the notes are rebuilt
                notes += "\n\n" + kept[0]
            if current.strip() != notes.strip():
                run("gh", "release", "edit", tag, "--notes", notes)
                print(f"{tag}: notes updated")
            behind = run("git", "rev-parse", f"{tag}^{{commit}}", check=False) != run("git", "rev-parse", "HEAD")
            if day == today and behind:  # today's release follows the newest commit and rebuilds the Docker image
                run("git", "tag", "-f", tag, "HEAD")
                run("git", "push", "-f", "origin", f"refs/tags/{tag}")
                run("gh", "workflow", "run", "docker.yml", "--ref", tag, *(["-f", "perf_override=true"] if why else []))
                print(f"{tag}: moved to {run('git', 'rev-parse', '--short', 'HEAD')}, image rebuilding")
            continue
        sha = run("git", "rev-parse", "HEAD") if day == today else \
            run("git", "log", "-1", "--format=%H", f"--before={day} 23:59:59")
        if not sha:
            print(f"{tag}: no commit on or before {day}, skipped")
            continue
        run("gh", "release", "create", tag, "--target", sha, "--title", changes.nice_date(day), "--notes", notes)
        print(f"{tag}: released at {sha[:7]}")


if __name__ == "__main__":
    main()
