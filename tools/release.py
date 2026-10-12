"""Publish one GitHub Release per day of updates in lightsout/data/changes.yaml (tag v<date>, e.g. v2026.10.12).

Past days are tagged at that day's last commit; today's at HEAD. A release that already exists gets its notes
refreshed if entries were added; today's also moves to HEAD and rebuilds the Docker image. Needs the gh CLI signed in. Run after pushing: .venv/bin/python tools/release.py
"""
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
    today = date.today().isoformat()
    for day, items in sorted(changes.by_date(changes.load()).items()):
        tag = "v" + day.replace("-", ".")
        notes = changes.day_notes(items) + "\n\nFull history: [CHANGELOG.md](CHANGELOG.md)"
        exists = subprocess.run(["gh", "release", "view", tag], cwd=ROOT, capture_output=True).returncode == 0
        if exists:
            current = run("gh", "release", "view", tag, "--json", "body", "--jq", ".body")
            if current.strip() != notes.strip():
                run("gh", "release", "edit", tag, "--notes", notes)
                print(f"{tag}: notes updated")
                if day == today:  # today's release moves to the newest commit and rebuilds the Docker image
                    run("git", "tag", "-f", tag, "HEAD")
                    run("git", "push", "-f", "origin", f"refs/tags/{tag}")
                    run("gh", "workflow", "run", "docker.yml", "--ref", tag)
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
