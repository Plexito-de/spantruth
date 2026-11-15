"""What moved between two nightly runs of the latest releases.

A new release that changes a cell is news. So this module compares two results documents, keeps
one history line per subject per day, and writes an Atom feed and a page. It posts nothing
anywhere: a person reads the feed and decides.

A cell that moved while every package stayed at its version moved because spantruth changed, so
it is not news about the library, and it is not in the feed. The history still shows it.
"""

from __future__ import annotations

import json
from collections import Counter
from html import escape
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from . import bugclasses, report

SITE = "https://plexito-de.github.io/spantruth"
UNGRADED = ("n/a", "inconclusive")


class WatchError(ValueError):
    """State that cannot be read. The run stops, because writing over it would lose the history."""


def _bug(cell: dict[str, Any]) -> str:
    """The name of the bug class of a cell's most severe failing check, or an empty string."""
    for check in cell.get("checks", ()):
        if check["verdict"] in ("pass", "n/a"):
            continue
        if bug := bugclasses.classify(check):
            return f"{bug.id} {bug.name}"
    return ""


def _failing_check(cell: dict[str, Any]) -> str:
    return next(
        (c["id"] for c in cell.get("checks", ()) if c["verdict"] not in ("pass", "n/a")), ""
    )


def _title(title: str, versions: dict[str, list[str]], fixed: int, broken: int) -> str:
    moved = ", ".join(f"{name} {new}" for name, (_old, new) in sorted(versions.items()))
    parts = []
    if fixed:
        parts.append(f"fixes {fixed} cell{'' if fixed == 1 else 's'}")
    if broken:
        parts.append(f"breaks {broken} cell{'' if broken == 1 else 's'}")
    return f"{title}: {moved} {' and '.join(parts)}"


def _issue(subject: str, scenario: str, check: str, known: list[dict[str, Any]]) -> str:
    for k in known:
        if k["subject"] == subject and scenario in k["scenarios"] and k["check"] == check:
            return k["url"]
    return ""


def changes(
    old: dict, new: dict, known: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """One record per library whose package versions moved and whose cells moved with them."""
    known = report.known_issues() if known is None else known
    out = []
    for name, result in new["subjects"].items():
        before = old["subjects"].get(name)
        if result["kind"] == "control" or before is None:
            continue
        versions = {
            package: [before["versions"][package], version]
            for package, version in result.get("versions", {}).items()
            if package in before.get("versions", {}) and before["versions"][package] != version
        }
        if not versions:
            continue
        fixed, broken = [], []
        for sid, cell in result["cells"].items():
            prior = before["cells"].get(sid)
            if prior is None or {prior["verdict"], cell["verdict"]} & set(UNGRADED):
                continue
            if prior["verdict"] == cell["verdict"]:
                continue
            was_passing, now_passing = prior["verdict"] == "pass", cell["verdict"] == "pass"
            source = prior if now_passing else cell
            check = _failing_check(source)
            record = {
                "scenario": sid,
                "check": check,
                "bug": _bug(source),
                "was": prior["verdict"],
                "now": cell["verdict"],
                "issue": _issue(name, sid, check, known),
            }
            if now_passing:
                fixed.append(record)
            elif was_passing:
                broken.append(record)
        if not fixed and not broken:
            continue
        out.append(
            {
                "id": f"tag:plexito-de.github.io,2026:spantruth/{new['created']}/{name}",
                "created": new["created"],
                "subject": name,
                "title": _title(result["title"], versions, len(fixed), len(broken)),
                "versions": versions,
                "fixed": fixed,
                "broken": broken,
            }
        )
    return out


def history_lines(doc: dict) -> list[dict[str, Any]]:
    """One line per library: its versions, its cell counts and its bug classes, for one day."""
    found = report.findings(doc)
    lines = []
    for name, result in doc["subjects"].items():
        if result["kind"] == "control":
            continue
        classes = {}
        for f in found:
            if f["subject"] == name and f["bug_class"]:
                classes.setdefault(f["bug_class"].id, set()).add(f["scenario"])
        lines.append(
            {
                "date": doc["created"][:10],
                "subject": name,
                "spantruth": doc["spantruth"],
                "versions": result.get("versions", {}),
                "cells": dict(Counter(c["verdict"] for c in result["cells"].values())),
                "classes": {k: len(v) for k, v in sorted(classes.items())},
            }
        )
    return lines


def merge_history(earlier: list[dict], new: list[dict]) -> list[dict]:
    """The history with today's lines replacing any earlier line of the same day and subject."""
    replaced = {(h["date"], h["subject"]) for h in new}
    return [h for h in earlier if (h["date"], h["subject"]) not in replaced] + new


def _rows(change: dict[str, Any]) -> list[str]:
    rows = []
    for label, items in (("fixed", change["fixed"]), ("broken", change["broken"])):
        for item in items:
            bug = f" ({item['bug']})" if item["bug"] else ""
            rows.append(
                f"{label}: {item['scenario']} {item['check']}{bug}: "
                f"was {item['was']}, now {item['now']}"
            )
    return rows


def feed(entries: list[dict[str, Any]]) -> str:
    """An Atom document, newest entry first. Every value goes through the XML escape."""
    ordered = sorted(entries, key=lambda e: e["created"], reverse=True)
    updated = ordered[0]["created"] if ordered else "2026-11-06T00:00:00+00:00"
    items = []
    for e in ordered:
        body = "\n".join(_rows(e))
        items.append(
            "<entry>"
            f"<title>{xml_escape(e['title'])}</title>"
            f'<link href="{SITE}/changes.html"/>'
            f"<id>{xml_escape(e['id'])}</id>"
            f"<updated>{xml_escape(e['created'])}</updated>"
            f"<content>{xml_escape(body)}</content>"
            "</entry>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>spantruth: new releases that change what LLM telemetry reports</title>"
        f'<link href="{SITE}/changes.html"/>'
        f'<link rel="self" href="{SITE}/feed.xml"/>'
        f"<id>{SITE}/feed.xml</id>"
        f"<updated>{xml_escape(updated)}</updated>"
        "<author><name>spantruth</name></author>"
        f"{''.join(items)}</feed>\n"
    )


def page(entries: list[dict[str, Any]]) -> str:
    """The same entries as a page, newest first. Every value is escaped."""
    from .site import CSS, ICON, _link

    blocks = []
    for e in sorted(entries, key=lambda e: e["created"], reverse=True):
        rows = []
        for label, items in (("Fixed", e["fixed"]), ("Broken", e["broken"])):
            for item in items:
                issue = f" {_link('upstream', item['issue'])}" if item.get("issue") else ""
                bug = f" · {escape(item['bug'])}" if item["bug"] else ""
                rows.append(
                    f"<li>{label}: <code>{escape(item['scenario'])}</code> "
                    f"{escape(item['check'])}{bug}: was {escape(item['was'])}, "
                    f"now {escape(item['now'])}.{issue}</li>"
                )
        blocks.append(
            f"<section><h2>{escape(e['title'])}</h2>"
            f'<p class="muted">{escape(e["created"][:10])}</p><ul>{"".join(rows)}</ul></section>'
        )
    body = "".join(blocks) or "<p>No release has changed a cell yet.</p>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>spantruth changes</title>
{ICON}
<link rel="alternate" type="application/atom+xml" href="feed.xml" title="spantruth changes">
<meta name="description" content="New releases of LLM instrumentation libraries that change what
they report, measured every night.">
<style>{CSS}</style></head>
<body><main>
<h1>What new releases changed</h1>
<p>Every night spantruth runs the latest release of each library. When a new release changes a
result, it shows up here. <a href="feed.xml">Subscribe to the feed</a>.</p>
<p class="muted"><a href="latest.html">Latest releases</a> · <a href="bugs.html">the ten bug
classes</a> · <a href="https://github.com/Plexito-de/spantruth">source</a></p>
{body}
</main></body></html>
"""


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except ValueError as exc:
        raise WatchError(f"{path}: {exc}") from None


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def run(current: Path, state: Path, out: Path) -> int:
    """Read the state, add this run to it, and write the files the site publishes."""
    new = json.loads(Path(current).read_text())
    history = _jsonl(state / "history.jsonl")
    entries = _jsonl(state / "changes.jsonl")
    previous = state / "latest.json"
    if previous.exists():
        try:
            old = json.loads(previous.read_text())
        except ValueError as exc:
            raise WatchError(f"{previous}: {exc}") from None
        known = {e["id"] for e in entries}
        entries += [c for c in changes(old, new) if c["id"] not in known]
    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out / "history.jsonl", merge_history(history, history_lines(new)))
    _write_jsonl(out / "changes.jsonl", entries)
    (out / "feed.xml").write_text(feed(entries))
    (out / "changes.html").write_text(page(entries))
    print(f"wrote {out}: {len(entries)} change(s) in the feed")
    return 0
