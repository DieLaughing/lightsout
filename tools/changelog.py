"""Rewrite CHANGELOG.md from lightsout/data/changes.yaml. Run after adding an entry: .venv/bin/python tools/changelog.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lightsout import changes  # noqa: E402

APP, SITE = "Lights Out", "https://github.com/DieLaughing/lightsout"

if __name__ == "__main__":
    (ROOT / "CHANGELOG.md").write_text(changes.markdown(changes.load(), APP, SITE))
    print("CHANGELOG.md updated")
