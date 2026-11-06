"""One self-contained HTML page for a results document. No scripts, no external assets.

Every string from a results document is escaped: attribute values come from the libraries
under test, so they are data, never markup. Colors are paired tokens, one value per scheme.
"""

from __future__ import annotations

from html import escape
from typing import Any

from . import bugclasses, impact
from .report import BUGS_URL, LEGEND, _subjects, findings

ICON = (
    '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' '
    "viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%231a6b37'/%3E"
    "%3Cpath d='M4 8l3 3 5-6' stroke='white' stroke-width='2' fill='none'/%3E%3C/svg%3E\">"
)
CSS = """
:root{--bg:#fbfbf9;--fg:#1b1d21;--muted:#545b66;--line:#e2e4e8;--card:#fff;
--pass:#1a6b37;--pass-bg:#e5f4ea;--missing:#6b4f00;--missing-bg:#fdf3d6;--wrong:#9a1f27;
--wrong-bg:#fbe5e6;--lost:#55267d;--lost-bg:#efe6f8;--broken:#7f3600;--broken-bg:#fdeadd;
--inc:#3f4853;--inc-bg:#eceef1;--link:#1a56b0}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--fg:#e8eaed;--muted:#a7aeb8;
--line:#2a2e35;--card:#181b20;--pass:#7fd49b;--pass-bg:#173322;--missing:#f1cf6b;
--missing-bg:#3a3014;--wrong:#ff9aa0;--wrong-bg:#3d1a1d;--lost:#cfaaf5;--lost-bg:#2c1e3d;
--broken:#ffb27c;--broken-bg:#3d2414;--inc:#c3c9d1;--inc-bg:#272b31;--link:#8ab4ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:19px;margin:40px 0 12px}
p{margin:8px 0;max-width:72ch}a{color:var(--link)}.muted{color:var(--muted)}code{font-size:13px}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%}th,td{padding:8px 10px;
border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
thead th{font-size:13px;color:var(--muted);font-weight:600}tbody th{font-weight:400}
tr:last-child td,tr:last-child th{border-bottom:0}td.c{text-align:center;white-space:nowrap}
.v{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600}
.pass{color:var(--pass);background:var(--pass-bg)}
.missing{color:var(--missing);background:var(--missing-bg)}
.wrong{color:var(--wrong);background:var(--wrong-bg)}.lost{color:var(--lost);background:var(--lost-bg)}
.broken{color:var(--broken);background:var(--broken-bg)}
.inconclusive{color:var(--inc);background:var(--inc-bg)}.na{color:var(--muted)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:10px 0 0}
dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}details{border:1px solid var(--line);
border-radius:10px;background:var(--card);padding:10px 14px;margin:8px 0}
summary{cursor:pointer}.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin:12px 0}
details:target{outline:2px solid var(--link);outline-offset:2px}td.c a{text-decoration:none}
.gist{display:inline-block;margin:2px 0 0 22px;color:var(--muted);font-size:13px;
overflow-wrap:anywhere}
.spans{margin:6px 0 0;font-size:13px}.spans table{margin:4px 0 10px;table-layout:fixed}
.spans td{padding:3px 8px;overflow-wrap:anywhere}.spans td:first-child{width:42%}
details code{overflow-wrap:anywhere}
.bug{display:inline-block;margin-left:6px;font-size:12px;font-weight:600;white-space:nowrap}
section{border-top:1px solid var(--line);padding:18px 0 6px;scroll-margin-top:12px}
section:target{outline:2px solid var(--link);outline-offset:6px;border-radius:4px}
section h2{margin:0 0 6px}.id{color:var(--muted);font-weight:400;font-size:14px;margin-right:8px}
.num{white-space:nowrap;text-align:right}
"""


def _url(value: Any) -> str | None:
    """A web address that is safe in an href. Anything else, such as javascript:, is not a link."""
    return value if isinstance(value, str) and value.startswith(("https://", "http://")) else None


def _link(text: str, url: Any) -> str:
    safe = _url(url)
    return f'<a href="{escape(safe)}">{text}</a>' if safe else text


def _badge(verdict: str) -> str:
    if verdict == "n/a":
        return '<span class="na" title="not graded">·</span>'
    return f'<span class="v {escape(verdict)}">{escape(verdict)}</span>'


def _matrix(doc: dict, names: list[str], first: dict[tuple[str, str], str]) -> str:
    """The scenario x subject table. A cell with a finding links to its first finding."""
    head = "".join(
        f'<th scope="col">{_link(escape(n), doc["subjects"][n]["homepage"])}</th>' for n in names
    )
    rows = []
    for sid, sc in doc["scenarios"].items():
        cells = "".join(
            '<td class="c">'
            + _linked(
                _badge(doc["subjects"][n]["cells"].get(sid, {}).get("verdict", "n/a")),
                first.get((n, sid)),
            )
            + "</td>"
            for n in names
        )
        rows.append(
            f'<tr><th scope="row"><code>{escape(sid)}</code><br>'
            f'<span class="muted">{escape(sc["title"])}</span></th>{cells}</tr>'
        )
    return (
        f'<div class="wrap"><table><thead><tr><th scope="col">Scenario</th>{head}</tr>'
        f"</thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _linked(badge: str, anchor: str | None) -> str:
    return f'<a href="#{escape(anchor)}">{badge}</a>' if anchor else badge


def _spans(spans: list[dict[str, Any]]) -> str:
    """The spans the library exported for the cell, every key and value escaped."""
    if not spans:
        return ""
    blocks = []
    for s in spans:
        rows = "".join(
            f"<tr><td><code>{escape(str(k))}</code></td><td><code>{escape(str(v))}</code></td></tr>"
            for k, v in sorted(s.get("attributes", {}).items())
        )
        blocks.append(
            f"<p><code>{escape(s.get('name', ''))}</code> "
            f'<span class="muted">{escape(s.get("scope", ""))}, status '
            f"{escape(s.get('status', ''))}</span></p><table>{rows}</table>"
        )
    return (
        f'<details class="spans"><summary>Exported spans ({len(spans)})</summary>'
        f"{''.join(blocks)}</details>"
    )


def _short(value: Any) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _gist(f: dict[str, Any]) -> str:
    """The finding in one line, for the summary that stays visible while the finding is closed."""
    parts = []
    if f.get("expected") is not None or f.get("got") is not None:
        parts.append(f"expected {_short(f.get('expected'))}, reported {_short(f.get('got'))}")
    if f.get("detail"):
        parts.append(f["detail"])
    text = ": ".join(parts)
    return text if len(text) <= 160 else text[:157] + "..."


# Link previews on the report's pages. The image is the repository's social preview.
SOCIAL = (
    '<meta property="og:image" content="https://raw.githubusercontent.com/Plexito-de/spantruth/'
    'main/docs/assets/social-preview.png">\n<meta name="twitter:card" '
    'content="summary_large_image">'
)


def _money(value: float) -> str:
    """A small dollar figure with three significant digits, such as $0.00785."""
    return f"${value:.3g}" if value < 1 else f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:+.1f}%".replace(".0%", "%")


def _cost(cost: impact.CellCost) -> str:
    """What the cell does to a cost figure, in words. Every value here comes from our own table."""
    if cost.reported == 0:
        lost = f"{_money(cost.real)} on the wire. " if cost.real else ""
        return f"{lost}Nothing reaches a backend ({_percent(cost.percent)}), at list price."
    return (
        f"{_money(cost.real)} on the wire, {_money(cost.reported)} from the spans "
        f"({_percent(cost.percent)}), at list price."
    )


def _finding(f: dict[str, Any], spans: list[dict[str, Any]]) -> str:
    rule = _link(escape(f.get("rule", "")), f.get("rule_url"))
    fields = [
        ("Expected", f.get("expected")),
        ("Reported", f.get("got")),
        ("Read from", f.get("where")),
        ("Note", f.get("detail") or None),
    ]
    rows = "".join(
        f"<dt>{k}</dt><dd><code>{escape(str(v))}</code></dd>" for k, v in fields if v is not None
    )
    issue = f.get("issue")
    if issue:
        note = f" {escape(issue['note'])}" if issue.get("note") else ""
        rows += f"<dt>Upstream</dt><dd>{_link(escape(issue['status']), issue['url'])}{note}</dd>"
    cost = f.get("cost")
    if cost is not None and cost.percent != 0:
        rows += f"<dt>Cost</dt><dd>{escape(_cost(cost))}</dd>"
    rows += f"<dt>Reproduce</dt><dd><code>{escape(f['reproduce'])}</code></dd>"
    anchor = escape(f["anchor"])
    bug = f.get("bug_class")
    chip = (
        f'<a class="bug" href="{BUGS_URL}#{bug.id}">{escape(bug.name)}</a>'
        if bug is not None
        else ""
    )
    return (
        f'<details id="{anchor}"><summary>{_badge(f["verdict"])} {escape(f["subject"])} · '
        f"<code>{escape(f['scenario'])}</code> · {escape(f['id'])}{chip} "
        f'<a href="#{anchor}" class="muted" title="link to this finding">#</a>'
        + (f'<br><span class="gist">{escape(gist)}</span>' if (gist := _gist(f)) else "")
        + "</summary>"
        f"<dl>{rows}<dt>Rule</dt><dd>{rule}</dd></dl>{_spans(spans)}</details>"
    )


def _versions(doc: dict, names: list[str]) -> str:
    items = []
    for n in names:
        s = doc["subjects"][n]
        pkgs = ", ".join(f"{k} {v}" for k, v in sorted(s.get("versions", {}).items()))
        err = ""
        if s.get("error"):
            err = f' <span class="v inconclusive">{escape(s["error"][:160])}</span>'
        notes = f" {escape(s['notes'])}" if s.get("notes") else ""
        items.append(
            f"<li><strong>{escape(n)}</strong>: {escape(s['title'])}.{notes} "
            f'<span class="muted">{escape(pkgs)}</span>{err}</li>'
        )
    return f"<ul>{''.join(items)}</ul>"


def _seen(found: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The findings of the libraries, per bug class."""
    out: dict[str, list[dict[str, Any]]] = {c.id: [] for c in bugclasses.CLASSES}
    for f in found:
        if f.get("bug_class") is not None:
            out[f["bug_class"].id].append(f)
    return out


def _counts(found: list[dict[str, Any]]) -> str:
    cells = len({(f["subject"], f["scenario"]) for f in found})
    libraries = len({f["subject"] for f in found})
    return (
        f"{cells} cell{'' if cells == 1 else 's'} in "
        f"{libraries} librar{'y' if libraries == 1 else 'ies'}"
    )


def _largest(found: list[dict[str, Any]]) -> float | None:
    """The cost error of largest size among these findings, or None when none has a figure."""
    costs = [f["class_cost"].percent for f in found if f["class_cost"] and f["class_cost"].percent]
    return max(costs, key=abs) if costs else None


def _class_table(found: list[dict[str, Any]]) -> str:
    seen = _seen(found)
    rows = []
    for c in bugclasses.CLASSES:
        if not seen[c.id]:
            continue
        largest = _largest(seen[c.id])
        rows.append(
            f'<tr><th scope="row"><a href="{BUGS_URL}#{c.id}">{c.id} {escape(c.name)}</a><br>'
            f'<span class="muted">{escape(c.summary)}</span></th>'
            f"<td>{escape(_counts(seen[c.id]))}</td>"
            f'<td class="num">{_percent(largest) if largest is not None else "—"}</td></tr>'
        )
    if not rows:
        return "<p>No bug class in this run.</p>"
    return (
        '<div class="wrap"><table><thead><tr><th scope="col">Bug</th><th scope="col">Seen in</th>'
        '<th scope="col" class="num">Largest cost error</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        f'<p class="muted">{_cost_note()}</p>'
    )


def _cost_note() -> str:
    sources = sorted({p.source for p in impact.load_prices().values()})
    links = " and ".join(_link(escape(u.split("/")[2]), u) for u in sources)
    return (
        f"A cost figure is for the token mix of one scenario, at list price from {links} on "
        f"{escape(impact.prices_as_of())}. Input tokens include cache reads and writes, so the "
        "uncached part is input minus reads minus writes. A span that never ends reports nothing, "
        "and usage on two spans counts twice only in a backend that sums across spans. A backend "
        "with another formula shows another number."
    )


def _example(f: dict[str, Any], page: str) -> str:
    gist = _gist(f) or f["id"]
    return (
        f'<a href="{escape(page)}#{escape(f["anchor"])}"><code>{escape(f["scenario"])}</code></a>: '
        f"{escape(gist)}"
    )


def render_bugs(doc: dict, findings_page: str = "index.html") -> str:
    """The ten classes, with what this run saw of each. It counts cells, and names no library."""
    every = [f for f in findings(doc) if doc["subjects"][f["subject"]]["kind"] != "control"]
    seen = _seen(every)
    sections = []
    for c in bugclasses.CLASSES:
        found = seen[c.id]
        facts = []
        if found:
            facts.append(f"<dt>In this run</dt><dd>{escape(_counts(found))}</dd>")
            largest = _largest(found)
            if largest is not None:
                facts.append(f"<dt>Largest cost error</dt><dd>{_percent(largest)}</dd>")
            facts.append(f"<dt>Example</dt><dd>{_example(found[0], findings_page)}</dd>")
        else:
            facts.append("<dt>In this run</dt><dd>Not seen</dd>")
        if c.remedy:
            facts.append(f"<dt>What to do today</dt><dd>{escape(c.remedy)}</dd>")
        if c.instances:
            links = ", ".join(
                f"{_link(escape(i.label), i.url)} (reported by {escape(i.credit)})"
                for i in c.instances
            )
            facts.append(f"<dt>In public</dt><dd>{links}</dd>")
        sections.append(
            f'<section id="{c.id}"><h2><span class="id">{c.id}</span>{escape(c.name)}</h2>'
            f"<p>{escape(c.summary)}</p><p>{escape(c.dashboard)}</p><dl>{''.join(facts)}</dl>"
            "</section>"
        )
    meta = f"spantruth {escape(doc['spantruth'])} · {escape(doc['created'])}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LLM telemetry bugs</title>
{ICON}
<meta property="og:title" content="The ten ways LLM telemetry goes wrong">
{SOCIAL}
<meta name="description" content="Ten ways LLM telemetry goes wrong: ghost spans, double spans,
phantom tokens and more, each measured against what the provider sent.">
<style>{CSS}</style></head>
<body><main>
<h1>The ten ways LLM telemetry goes wrong</h1>
<p>Your cost dashboard, your token budget and your error rate all read the numbers that an
instrumentation library writes on a span. When one of those numbers is wrong, nothing fails. The
dashboard is just wrong, with full confidence. These are the classes of bug that spantruth measures,
each one checked against what the provider actually sent.</p>
<p>To see which of them your own versions have, run <code>uvx spantruth doctor</code> in your
project.</p>
<p class="muted">{meta} · <a href="{escape(findings_page)}">the full report</a> ·
<a href="https://github.com/Plexito-de/spantruth">source</a></p>
{"".join(sections)}
<p class="muted">{_cost_note()}</p>
</main></body></html>
"""


def render(doc: dict) -> str:
    names = _subjects(doc)
    legend = "".join(f"<span>{_badge(k)} {escape(v)}</span>" for k, v in LEGEND.items())
    # The controls' failures are planted and checked in CI, so the list shows the libraries only.
    every = [f for f in findings(doc) if doc["subjects"][f["subject"]]["kind"] != "control"]
    first: dict[tuple[str, str], str] = {}
    for f in every:
        first.setdefault((f["subject"], f["scenario"]), f["anchor"])
    found = "".join(
        _finding(f, doc["subjects"][f["subject"]]["cells"][f["scenario"]].get("spans", []))
        for f in every
    )
    found = found or "<p>No findings.</p>"
    meta = (
        f"spantruth {escape(doc['spantruth'])} · {escape(doc['mode'])} versions · "
        f"{escape(doc['created'])} · Python {escape(doc['python'])} · "
        f"semantic-conventions-genai <code>{escape(doc['semconv_genai'])}</code>"
        + "".join(f" · with <code>{escape(o)}</code>" for o in doc.get("overrides", []))
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>spantruth report</title>
{ICON}
<meta property="og:title" content="spantruth: does your LLM telemetry tell the truth?">
{SOCIAL}
<meta name="description" content="Does LLM telemetry match what the provider sent?
Wire traffic replayed through instrumentation libraries, checked value by value.">
<style>{CSS}</style></head>
<body><main>
<h1>spantruth</h1>
<p>Does the telemetry an LLM instrumentation library reports match what the provider actually
sent? Each cell replays one provider response through one library and checks every reported value
against the wire.</p>
<p class="muted">{meta} · <a href="https://github.com/Plexito-de/spantruth">source</a></p>
<p>Pages: <a href="index.html">pinned versions</a> · <a href="latest.html">latest releases</a> ·
<a href="bugs.html">the ten bug classes</a>. A fix that ships upstream turns its cell green on
the latest-releases page first.</p>
<div class="legend">{legend}</div>
{_matrix(doc, names, first)}
<p class="muted">The controls run the suite's own reference instrumentation.
<code>control-honest</code> has to pass every check, and <code>control-faulty</code> has to fail
exactly its planted faults. If either moves, the suite is wrong, not a library. CI checks both,
so their cells are not in the findings below.</p>
<h2>Bug classes in this run</h2>
{_class_table(every)}
<h2>Findings</h2>
<p class="muted">Every finding cites the rule it applies. To dispute one, open an issue: a disputed
finding is marked as such in <a href="https://github.com/Plexito-de/spantruth/blob/main/known_issues.toml">known_issues.toml</a>.</p>
{found}
<h2>Subjects and versions</h2>
{_versions(doc, names)}
</main></body></html>
"""
