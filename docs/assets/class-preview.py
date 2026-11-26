"""Purpose: render the 1200x630 link preview of each bug class page into docs/assets/cards/.
Usage: uv run python docs/assets/class-preview.py   (needs Node, for `npx playwright screenshot`)

The site build only links the committed PNGs, so it needs neither Node nor a browser. Run this
again when a class's name, symptom or summary changes, and commit the PNGs.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

from spantruth import bugclasses

HERE = Path(__file__).resolve().parent
# The verdict color that the class's findings usually carry on the report, dark scheme.
ACCENT = {
    "ST01": "#cfaaf5",
    "ST02": "#ff9aa0",
    "ST03": "#f1cf6b",
    "ST04": "#ff9aa0",
    "ST05": "#f1cf6b",
    "ST06": "#f1cf6b",
    "ST07": "#ff9aa0",
    "ST08": "#ff9aa0",
    "ST09": "#f1cf6b",
    "ST10": "#ffb27c",
}


def page(bug: bugclasses.BugClass) -> str:
    fields = {
        "accent": ACCENT[bug.id],
        "id": bug.id,
        "name": escape(bug.name),
        "symptom": escape(bugclasses.PAGES[bug.id].symptom),
        "summary": escape(bug.summary),
        # The first sentence of what the dashboard shows.
        "dashboard": escape(bug.dashboard.split(". ")[0].rstrip(".") + "."),
    }
    html = (HERE / "class-preview.html").read_text()
    for key, value in fields.items():
        html = html.replace("{{" + key + "}}", value)
    return html


def main() -> int:
    out = HERE / "cards"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for bug in bugclasses.CLASSES:
            source = Path(tmp) / f"{bug.id}.html"
            source.write_text(page(bug))
            png = out / f"{bugclasses.slug(bug)}.png"
            subprocess.run(
                [
                    "npx",
                    "-y",
                    "playwright@1.58",
                    "screenshot",
                    "--viewport-size=1200,630",
                    source.as_uri(),
                    str(png),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            print(png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
