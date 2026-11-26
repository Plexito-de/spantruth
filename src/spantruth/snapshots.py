"""Dated copies of the report, so a link in an upstream issue still resolves after the fix lands.

Each nightly run adds `snapshots/<date>/`. A copy of the four pages is about 3.4 MB, so a full copy
is written only when the results changed. A day with the same results as the last copy gets four
small pages that forward to that copy, with the finding anchor. The bound: at most MAX_COPIES full
copies (about 205 MB at today's size, inside the 1 GB of a GitHub Pages site) and MAX_DATES dated
entries. The oldest go first.

The workflow restores the kept copies from the live site before this runs, the same way it
restores the history, and this module writes the site's `snapshots/` from them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from html import escape
from pathlib import Path
from typing import Any

from . import report

SCHEMA = 1
PAGES = ("index.html", "latest.html", "bugs.html", "changes.html")
MAX_COPIES = 60
MAX_DATES = 730
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class SnapshotError(ValueError):
    """An index that cannot be read. The run stops, because writing over it would lose copies."""


def fingerprint(*docs: dict[str, Any]) -> str:
    """What a reader of the report sees: every subject's versions and every finding's values."""
    seen = []
    for doc in docs:
        for name, result in sorted(doc["subjects"].items()):
            seen.append([doc["mode"], name, sorted(result.get("versions", {}).items())])
        for f in report.findings(doc, known=[]):
            seen.append(
                [doc["mode"], f["subject"], f["scenario"], f["id"], f["verdict"]]
                + [str(f.get(k)) for k in ("expected", "got", "detail")]
            )
    return hashlib.sha256(json.dumps(seen, sort_keys=True).encode()).hexdigest()[:16]


def load_index(path: Path) -> list[dict[str, str]]:
    """The entries of a published index, oldest first. A missing index is the first run."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        entries = data["snapshots"]
        for e in entries:
            if not (DATE.fullmatch(e["date"]) and DATE.fullmatch(e["copy"])):
                raise ValueError(f"not a date: {e['date']!r} or {e['copy']!r}")
    except (ValueError, KeyError, TypeError) as exc:
        raise SnapshotError(f"{path}: {exc}") from None
    return sorted(entries, key=lambda e: e["date"])


def plan(entries: list[dict[str, str]], date: str, print_: str) -> list[dict[str, str]]:
    """The entries after today's run: today's is a copy, or points at the copy it equals."""
    entries = [e for e in entries if e["date"] != date]
    last = entries[-1] if entries else None
    copy = last["copy"] if last and last["fingerprint"] == print_ else date
    entries.append({"date": date, "copy": copy, "fingerprint": print_})
    copies = sorted({e["copy"] for e in entries})[-MAX_COPIES:]
    return [e for e in entries if e["copy"] in copies][-MAX_DATES:]


def _banner(html: str, date: str) -> str:
    """The copied page with a line on top that says it is a copy, and kept out of search."""
    note = (
        f'<p><span class="v missing">Snapshot of {escape(date)}</span> The report as it was on '
        "this day. It does not change. "
        '<a href="../../index.html">The current report</a> · '
        '<a href="../index.html">all snapshots</a></p>'
    )
    html = html.replace("<head>", '<head><meta name="robots" content="noindex">', 1)
    if "<body><main>" in html:
        return html.replace("<body><main>", "<body><main>" + note, 1)
    return html.replace("<body>", "<body>" + note, 1)


def _forward(date: str, copy: str, page: str) -> str:
    """A small page that forwards to the copy with the same results, and keeps the anchor."""
    target = f"../{copy}/{page}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex">
<title>spantruth snapshot {date}</title>
<meta http-equiv="refresh" content="0; url={target}">
<script>location.replace({json.dumps(target)} + location.hash)</script></head>
<body><p>The results on {date} were the same as on {copy}.
<a href="{target}">Open the copy of {copy}</a>.</p></body></html>
"""


def page(entries: list[dict[str, str]]) -> str:
    from .site import CSS, ICON

    example = escape(entries[-1]["date"]) if entries else "DATE"
    rows = []
    for e in reversed(entries):
        d, c = escape(e["date"]), escape(e["copy"])
        same = "" if e["copy"] == e["date"] else f' <span class="muted">same results as {c}</span>'
        rows.append(
            f'<tr><th scope="row">{d}</th><td><a href="{d}/index.html">pinned versions</a> · '
            f'<a href="{d}/latest.html">latest releases</a>{same}</td></tr>'
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>spantruth snapshots</title>
{ICON}
<meta name="description" content="Dated copies of the spantruth report, so a link to a finding
still shows what it showed on the day.">
<style>{CSS}</style></head>
<body><main>
<h1>Snapshots of the report</h1>
<p>Every night keeps a dated copy of the report. A link to a dated copy shows the finding as it
was on that day, also after the library fixes it. To link a finding, open its <code>#</code> link
on a dated copy, such as <code>snapshots/{example}/index.html#...</code>.</p>
<p class="muted">The site keeps the last {MAX_COPIES} copies with different results and the last
{MAX_DATES} dates. A date with the same results as the copy before it forwards to that copy.
<a href="../index.html">The current report</a> ·
<a href="https://github.com/Plexito-de/spantruth">source</a></p>
<div class="wrap"><table><thead><tr><th scope="col">Date</th><th scope="col">Pages</th></tr>
</thead><tbody>{"".join(rows)}</tbody></table></div>
</main></body></html>
"""


def run(site: Path, state: Path, date: str | None = None) -> int:
    """Add today's copy to the kept ones in `state`, and write the site's `snapshots/`."""
    pinned = json.loads((site / "pinned.json").read_text())
    latest = json.loads((site / "latest.json").read_text())
    date = date or pinned["created"][:10]
    if not DATE.fullmatch(date):
        raise SnapshotError(f"not a date: {date!r}")
    entries = plan(load_index(state / "index.json"), date, fingerprint(pinned, latest))
    out = site / "snapshots"
    out.mkdir(parents=True, exist_ok=True)
    for copy in sorted({e["copy"] for e in entries}):
        target = out / copy
        target.mkdir(exist_ok=True)
        for name in PAGES:
            source = site / name if copy == date else state / copy / name
            if not source.is_file():
                raise SnapshotError(f"{source} is missing, so the copy of {copy} would be lost")
            if copy == date:
                (target / name).write_text(_banner(source.read_text(), date))
            else:
                shutil.copyfile(source, target / name)
    for e in entries:
        if e["copy"] != e["date"]:
            (out / e["date"]).mkdir(exist_ok=True)
            for name in PAGES:
                (out / e["date"] / name).write_text(_forward(e["date"], e["copy"], name))
    index = {"schema": SCHEMA, "snapshots": entries}
    (out / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    (out / "index.html").write_text(page(entries))
    copies = len({e["copy"] for e in entries})
    print(f"wrote {out}: {len(entries)} date(s), {copies} full copies")
    return 0


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--site", required=True, help="the built site, with pinned.json")
    parser.add_argument(
        "--state", required=True, help="the published snapshots/ with index.json and the copies"
    )
    parser.add_argument("--date", help="the date of this copy (default: the run's date)")


def main(args: argparse.Namespace) -> int:
    return run(Path(args.site), Path(args.state), args.date)
