"""One self-contained HTML page for a results document. No scripts, no external assets.

Every string from a results document is escaped: attribute values come from the libraries
under test, so they are data, never markup. Colors are paired tokens, one value per scheme.
"""

from __future__ import annotations

import re
from datetime import date
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
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;
overflow-x:auto}pre code{font-size:14px}.libs li{margin:10px 0;overflow-wrap:anywhere}
@media (max-width:640px){.stack table,.stack tbody,.stack tr,.stack th,.stack td{display:block}
.stack thead{display:none}.stack tr{border-bottom:1px solid var(--line);padding:8px 0}
.stack th,.stack td{border:0;padding:2px 12px}.stack td[data-label]::before{
content:attr(data-label) ": ";color:var(--muted)}}
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
SITE = "https://plexito-de.github.io/spantruth/"
ASSETS = "https://raw.githubusercontent.com/Plexito-de/spantruth/main/docs/assets/"


def _head(title: str, description: str, path: str, card: tuple[str, str] | None = None) -> str:
    """Everything up to <main>: a title, a description, the canonical address and a link preview.

    `card` is a 1200x630 preview under docs/assets/ and its alternative text. Without one, the
    page shows the repository's social preview. Every argument is spantruth's own text, and is
    escaped all the same.
    """
    title, description = escape(title), escape(description)
    url = escape(SITE + path)
    image = f'<meta property="og:image" content="{escape(ASSETS)}social-preview.png">'
    if card:
        image = (
            f'<meta property="og:image" content="{escape(ASSETS + card[0])}">\n'
            '<meta property="og:image:width" content="1200">\n'
            '<meta property="og:image:height" content="630">\n'
            f'<meta property="og:image:alt" content="{escape(card[1])}">'
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{url}">
{ICON}
<meta property="og:type" content="website">
<meta property="og:site_name" content="spantruth">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
{image}
<meta name="twitter:card" content="summary_large_image">
<style>{CSS}</style></head>
<body><main>
"""


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
        "uncached part is input minus reads minus writes. A cache write that lasts 1 hour costs "
        "more than one that lasts 5 minutes. No span attribute separates the two, so the figure "
        "from the spans uses the split on the wire. A span that never ends reports nothing, "
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
        page = bugclasses.PAGES[c.id]
        sections.append(
            f'<section id="{c.id}"><h2><span class="id">{c.id}</span>{escape(c.name)}</h2>'
            f"<p>{escape(c.summary)}</p><p>{escape(c.dashboard)}</p><dl>{''.join(facts)}</dl>"
            f'<p><a href="bugs/{bugclasses.slug(c)}.html">{escape(page.symptom)}</a>: what '
            "causes it, which versions show it, and how to fix it</p></section>"
        )
    meta = f"spantruth {escape(doc['spantruth'])} · {escape(doc['created'])}"
    head = _head(
        "The ten ways LLM telemetry goes wrong",
        "Ten ways LLM telemetry goes wrong: ghost spans, double spans, phantom tokens and more, "
        "each measured against what the provider sent.",
        "bugs.html",
    )
    return f"""{head}<h1>The ten ways LLM telemetry goes wrong</h1>
<p>Your cost dashboard, your token budget and your error rate all read the numbers that an
instrumentation library writes on a span. When one of those numbers is wrong, nothing fails. The
dashboard is just wrong, with full confidence. These are the classes of bug that spantruth measures,
each one checked against what the provider actually sent.</p>
<p>To see which of them your own versions have, run <code>uvx spantruth doctor</code> in your
project.</p>
<p class="muted">{meta} · <a href="{escape(findings_page)}">the full report</a> ·
<a href="fixes.html">fixed upstream</a> ·
<a href="method.html">how we know a check is right</a> ·
<a href="https://github.com/Plexito-de/spantruth">source</a></p>
{"".join(sections)}
<p class="muted">{_cost_note()}</p>
</main></body></html>
"""


def _thread(url: str) -> str:
    """`repo#12` for a GitHub issue or pull request, the host for any other address."""
    match = re.match(r"https://github\.com/[^/]+/([^/]+)/(?:issues|pull)/(\d+)", url)
    return f"{match[1]}#{match[2]}" if match else url.split("/")[2]


def _person(login: str) -> str:
    return _link(f"@{escape(login)}", f"https://github.com/{login}")


def _day(value: str) -> str:
    d = date.fromisoformat(value)
    return f"{d:%B} {d.day}, {d.year}"


def _releases(issue: dict[str, Any]) -> str:
    """`pkg 3.1.0` for each `pkg>=3.1.0` in `fixed_in`."""
    return ", ".join(re.sub(r"\s*(>=|==|~=|>)\s*", " ", s) for s in issue["fixed_in"])


def _fix_anchor(issue: dict[str, Any]) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", _thread(issue["url"]))


def _run_day(created: Any) -> str:
    """`November 26, 2026` for a run's timestamp, or the value as it is when it is not one."""
    try:
        return _day(str(created)[:10])
    except ValueError:
        return str(created)


def _fix(issue: dict[str, Any]) -> str:
    """One fix: the release that has it, the bug it removes, who reported it and who fixed it."""
    days = (date.fromisoformat(issue["fixed_on"]) - date.fromisoformat(issue["reported_on"])).days
    bug = bugclasses.classify({"id": issue["check"]})
    chip = (
        f'<p><a href="bugs/{bugclasses.slug(bug)}.html">{bug.id} {escape(bug.name)}</a>: '
        f"{escape(bug.summary)}</p>"
        if bug
        else ""
    )
    upgrade = " ".join(f'"{s}"' for s in issue["fixed_in"])
    return (
        f'<section id="{escape(_fix_anchor(issue))}"><h2>{escape(_releases(issue))}</h2>{chip}<dl>'
        f"<dt>Reported</dt><dd>{_link(escape(_thread(issue['url'])), issue['url'])} by "
        f"{_person(issue['reported_by'])}, {_day(issue['reported_on'])}</dd>"
        f"<dt>Fixed</dt><dd>{_link(escape(_thread(issue['fix_url'])), issue['fix_url'])} by "
        f"{_person(issue['fixed_by'])}, merged {_day(issue['fixed_on'])}</dd>"
        f"<dt>Report to merge</dt><dd>{days} day{'' if days == 1 else 's'}</dd>"
        f"<dt>To get it</dt><dd><code>pip install {escape(upgrade)}</code></dd>"
        "</dl></section>"
    )


def render_fixes(known: list[dict[str, Any]]) -> str:
    """Every upstream fix of a known issue, newest first, with who reported it and who fixed it."""
    done = [k for k in known if k["status"] == "fixed-upstream"]
    done.sort(key=lambda k: (k["fixed_on"], k["url"]), reverse=True)
    sections = "".join(_fix(k) for k in done) or "<p>No upstream fix yet.</p>"
    count = f"{len(done)} fix{'' if len(done) == 1 else 'es'}"
    head = _head(
        "Telemetry bugs fixed upstream, with credit",
        "LLM telemetry bugs that library maintainers and contributors fixed: who reported "
        "each one, who wrote the fix, and the first release that has it.",
        "fixes.html",
    )
    return f"""{head}<h1>Fixed upstream</h1>
<p>Each bug below was fixed by the people who maintain and contribute to an instrumentation
library. spantruth reproduces each bug. They did the work that removed it. Each entry names
who reported the bug, who wrote the fix, and the first release that has it. Thank you to all of
them.</p>
<p>To get a fix, upgrade to the release it names. To see which bugs your own versions still have,
run <code>uvx spantruth doctor</code> in your project.</p>
<p class="muted">{count} · <a href="index.html">the report</a> ·
<a href="bugs.html">the ten bug classes</a> ·
<a href="method.html">how we know a check is right</a> ·
<a href="https://github.com/Plexito-de/spantruth/blob/main/known_issues.toml">the data</a></p>
{sections}
<p class="muted">A fix that is missing here? Add it to known_issues.toml in a pull request, with
the issue, the fix and the release.</p>
</main></body></html>
"""


def class_description(bug: bugclasses.BugClass) -> str:
    """The search snippet of a class page: what goes wrong, then what the page answers."""
    return f"{bug.summary} Which library versions show it, how to check yours, how to fix it."


# Packages that every subject runs. A class page names them only as the SDK a finding used.
_SDKS = ("anthropic", "openai")
_HARNESS = ("opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-http")


def _library_versions(subject: dict, providers: set[str]) -> str:
    """`pkg 1.0, with anthropic 2.4.0`: the library's own packages, then the SDKs it wrapped."""
    versions = subject.get("versions", {})
    own = [f"{k} {v}" for k, v in sorted(versions.items()) if k not in _SDKS + _HARNESS]
    sdks = [f"{k} {versions[k]}" for k in _SDKS if k in providers and k in versions]
    if own and sdks:
        return f"{', '.join(own)}, with {', '.join(sdks)}"
    return ", ".join(own or sdks)


def _shown_by(doc: dict, found: list[dict[str, Any]]) -> str:
    """Each library that shows the class, in name order, with its versions and its findings."""
    by_subject: dict[str, dict[str, str]] = {}
    for f in found:
        by_subject.setdefault(f["subject"], {}).setdefault(f["scenario"], f["anchor"])
    items = []
    for name in sorted(by_subject):
        cells = by_subject[name]
        providers = {sid.split("/")[0] for sid in cells}
        versions = _library_versions(doc["subjects"][name], providers)
        links = ", ".join(
            f'<a href="../index.html#{escape(anchor)}"><code>{escape(sid)}</code></a>'
            for sid, anchor in cells.items()
        )
        count = f"{len(cells)} scenario{'' if len(cells) == 1 else 's'}"
        items.append(
            f"<li><strong>{escape(name)}</strong> "
            f'<span class="muted">{escape(versions)}</span><br>{count}: {links}</li>'
        )
    return f'<ul class="libs">{"".join(items)}</ul>'


def _class_fixes(bug: bugclasses.BugClass, known: list[dict[str, Any]]) -> str:
    fixes = [
        k
        for k in known
        if k["status"] == "fixed-upstream"
        and (c := bugclasses.classify({"id": k["check"]}))
        and c.id == bug.id
    ]
    items = "".join(
        f'<li><a href="../fixes.html#{escape(_fix_anchor(k))}">{escape(_releases(k))}</a>, '
        f"fixed by {_person(k['fixed_by'])}</li>"
        for k in sorted(fixes, key=lambda k: k["fixed_on"], reverse=True)
    )
    return f"<p>Fixed upstream in these releases:</p><ul>{items}</ul>" if items else ""


def render_class(doc: dict, bug: bugclasses.BugClass, known: list[dict[str, Any]]) -> str:
    """One bug class, titled by what a user sees, with the libraries that show it in this run."""
    page = bugclasses.PAGES[bug.id]
    every = [f for f in findings(doc, []) if doc["subjects"][f["subject"]]["kind"] != "control"]
    found = _seen(every)[bug.id]
    mode = "latest releases" if doc.get("mode") == "latest" else "pinned versions"
    run = (
        f"In the run of {escape(_run_day(doc['created']))}, at the {mode}, with spantruth "
        f"{escape(doc['spantruth'])}"
    )
    if found:
        shown = (
            f"<p>{run}. Each scenario links to its finding, with the exported spans and the "
            'command that reproduces it. The <a href="../latest.html">latest releases</a> can '
            f"differ.</p>{_shown_by(doc, found)}"
        )
    else:
        shown = (
            f"<p>{run}, no library shows it. The "
            '<a href="../latest.html">latest releases</a> can differ.</p>'
        )
    reported = "".join(
        f"<li>{_link(escape(i.label), i.url)}, reported by {escape(i.credit)}</li>"
        for i in bug.instances
    )
    article = (
        f'<p>The long version, with measurements: <a href="../{escape(page.article)}">'
        f"the write-up</a>.</p>"
        if page.article
        else ""
    )
    slug = bugclasses.slug(bug)
    card = (f"cards/{slug}.png", f"{page.symptom}. {bug.summary}")
    head = _head(page.symptom, class_description(bug), f"bugs/{slug}.html", card)
    name = f"{bug.id} {escape(bug.name)}"
    return f"""{head}<p class="muted"><a href="../bugs.html#{bug.id}">{name}</a>, one of
<a href="../bugs.html">ten ways LLM telemetry goes wrong</a></p>
<h1>{escape(page.symptom)}</h1>
<p>{escape(bug.summary)} {escape(bug.dashboard)}</p>
<h2>What causes it</h2>
<p>{escape(page.cause)}</p>
<h2>Which versions show it</h2>
{shown}
<h2>How to check your project</h2>
<p>Run this in your project's directory:</p>
<pre><code>uvx spantruth doctor</code></pre>
<p>It reads your lock file, or your requirements if you have no lock file, and tests the
libraries it recognizes, at your versions, against a local fake provider. It needs no API key and
does not run your code. The first run downloads those library versions into environments of
its own. A finding of this kind is named <strong>{name}</strong>.</p>
<h2>How to fix it</h2>
<p>{escape(page.fix)}</p>
{_class_fixes(bug, known)}{article}
{f"<p>Reported in public:</p><ul>{reported}</ul>" if reported else ""}
<p class="muted"><a href="../index.html">the report</a> · <a href="../fixes.html">fixed
upstream</a> · <a href="https://github.com/Plexito-de/spantruth">source</a></p>
</main></body></html>
"""


def sitemap() -> str:
    """Every page of the site, by its full address. The site lives under one path."""
    paths = [
        "index.html",
        "latest.html",
        "bugs.html",
        "fixes.html",
        "method.html",
        "changes.html",
        "double-spans.html",
        *(f"bugs/{bugclasses.slug(c)}.html" for c in bugclasses.CLASSES),
    ]
    urls = "".join(f"<url><loc>{escape(SITE + p)}</loc></url>\n" for p in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n'
    )


REPO = "https://github.com/Plexito-de/spantruth"
# The faults that control-faulty switches on in reference.py: what each one does wrong, and the
# checks that must catch it. A test holds this to subjects/control-faulty/instrument.py.
FAULTS = {
    "anthropic-raw-input": (
        "Copies Anthropic's input_tokens as it is, without the cache reads and writes.",
        ("input_tokens", "metric.input_tokens"),
    ),
    "close-leaks-span": (
        "Does not end the span when the app closes a stream or stops reading it early.",
        ("span.ended",),
    ),
    "content-first-chunk": (
        "Records only the first chunk of a stream as the completion text.",
        ("content.output_matches",),
    ),
    "content-ignores-off": (
        "Records prompt and completion text with the library's switch set to off.",
        ("content.off_respected",),
    ),
    "gemini-candidates-output": (
        "Reports Gemini's candidatesTokenCount alone as the output, without the thinking tokens.",
        ("output_tokens", "metric.output_tokens"),
    ),
    "errors-as-success": (
        "Ends the span of a failed call without status ERROR and without error.type.",
        ("error.status", "error.type"),
    ),
    "estimate-tokens": (
        "Guesses the output count from the length of the text when a stream carries no usage.",
        ("output_tokens", "metric.output_tokens"),
    ),
    "metric-reasoning-left-out": (
        "Leaves the reasoning tokens out of the output token metric, while the span counts them.",
        ("metric.output_tokens",),
    ),
    "retry-as-error": (
        "Marks a call as failed when it succeeded after the SDK retried a 429.",
        ("status.unset",),
    ),
}


def _control_verdicts(doc: dict) -> str:
    """What the two controls did in this run, compared by the code that CI uses."""
    from . import expectations, subjects

    out = []
    honest = doc["subjects"].get("control-honest")
    if honest is None:
        out.append("control-honest is not in this run.")
    elif expectations.failing(honest):
        out.append("control-honest moved: it failed a check. The suite is wrong until it is fixed.")
    else:
        n = len(honest["cells"])
        out.append(f"control-honest passed every check in {n} scenario{'' if n == 1 else 's'}.")
    faulty = doc["subjects"].get("control-faulty")
    if faulty is None:
        out.append("control-faulty is not in this run.")
    else:
        expected = expectations.load(subjects.SUBJECTS / "control-faulty" / "expected.toml")
        moved = expectations.compare(
            expected, expectations.failing(faulty), ran=set(faulty["cells"])
        )
        out.append(
            f"control-faulty moved in {len(moved)} check{'' if len(moved) == 1 else 's'}. "
            "The suite is wrong until it is fixed."
            if moved
            else "control-faulty failed exactly its planted faults, and passed every other check."
        )
    return " ".join(out)


def _fault_rows() -> tuple[str, str, int]:
    """The table of planted faults, the size of the expectation file, and the checks it names."""
    from . import expectations, subjects

    expected = expectations.load(subjects.SUBJECTS / "control-faulty" / "expected.toml")
    rows = []
    for name, (what, checks) in FAULTS.items():
        n = sum(1 for found in expected.values() if any(c in found for c in checks))
        caught = ", ".join(f"<code>{escape(c)}</code>" for c in checks)
        rows.append(
            f'<tr><th scope="row"><code>{escape(name)}</code></th><td>{escape(what)}</td>'
            f'<td data-label="Must fail">{caught}, in {n} scenario{"" if n == 1 else "s"}</td></tr>'
        )
    total = sum(len(found) for found in expected.values())
    planted = len({c for found in expected.values() for c in found})
    return "".join(rows), f"{total} failing checks in {len(expected)} scenarios", planted


def _rule_rows() -> tuple[str, int, int]:
    """The table of checks and rules, the number that cite a specification, and the total."""
    from .rules import RULES

    rows = []
    for check, (text, url) in RULES.items():
        source = _link("the spec", url) if _url(url) else "spantruth's own rule"
        rows.append(
            f'<tr><th scope="row"><code>{escape(check)}</code></th><td>{escape(text)}</td>'
            f'<td data-label="Source">{source}</td></tr>'
        )
    return "".join(rows), sum(1 for _, url in RULES.values() if url), len(RULES)


def render_method(doc: dict, known: list[dict[str, Any]]) -> str:
    """Why a finding can be trusted, for a maintainer who doubts one, and how to dispute it."""
    from .scenarios import SCENARIOS

    faults, size, planted = _fault_rows()
    rule_rows, cited, total = _rule_rows()
    disputed = sum(1 for k in known if k["status"] == "disputed")
    head = _head(
        "How we know a spantruth check is right",
        "Two controls, planted faults, a cited rule behind every check, fixtures checked with the "
        "SDK types, offline runs, and the way to dispute a finding.",
        "method.html",
    )
    return f"""{head}<h1>How we know a check is right</h1>
<p>A red cell on the report is a claim about somebody's work. This page says why the claim holds,
and how to show us that it does not. The controls and the fixture tests below run in CI on
every pull request.</p>
<p class="muted"><a href="index.html">the report</a> · <a href="bugs.html">the ten bug classes</a> ·
<a href="fixes.html">fixed upstream</a> · <a href="{REPO}">source</a></p>
<h2>Two controls run in every report</h2>
<p>Both controls run spantruth's own reference instrumentation,
<a href="{REPO}/blob/main/src/spantruth/reference.py">reference.py</a>, through every scenario,
beside the libraries. <code>control-honest</code> runs it as written and has to pass every check.
<code>control-faulty</code> runs it with the faults below switched on and has to fail exactly the
checks in its <a href="{REPO}/blob/main/subjects/control-faulty/expected.toml">expectation file</a>
({size}), and pass every other one. If either control moves, a check or the harness is wrong, not
a library. The expectation file is never edited to make a test pass.</p>
<p><strong>In this run:</strong> {escape(_control_verdicts(doc))}</p>
<div class="wrap stack"><table><thead><tr><th scope="col">Planted fault</th><th scope="col">What it
does wrong</th><th scope="col">Must fail</th></tr></thead><tbody>{faults}</tbody></table></div>
<p>The planted faults make {planted} of the {total} checks fail on purpose. A new check gets a
planted fault in the same pull request.</p>
<h2>Every check cites a rule</h2>
<p>A finding names its check and links the rule it applies. {cited} of the {total} checks apply a
sentence of a specification, most of them linked at a fixed commit or version, so
the link shows the sentence we read. The others are spantruth's own rules, for what no
specification states, such as that a call produces a span at all. They are written out here, so
you can disagree with them.</p>
<div class="wrap stack"><table><thead><tr><th scope="col">Check</th><th scope="col">Rule</th>
<th scope="col">Source</th></tr></thead><tbody>{rule_rows}</tbody></table></div>
<h2>The fixtures are the provider's wire format</h2>
<p>Each of the {len(SCENARIOS)} scenarios is a provider response built to the provider's wire
format, and one is replayed from a recorded stream. A test parses
every fixture with the provider's own SDK types (the <code>openai</code> and
<code>anthropic</code> packages) and fails on any field the types do not declare. A fixture value
is never changed to make a library pass. The token counts in one fixture are all different from
each other, so a library that reads the wrong field cannot pass by chance.</p>
<h2>Runs are offline and repeatable</h2>
<p>The provider is a local server that replays fixed responses: no API key, no model and no
sampling. Each library runs at the versions pinned in its <code>subject.toml</code>, in its own
environment. Its process gets a proxy address that goes nowhere, so a client that honors the proxy
variables fails fast instead of calling out. A client that opens raw sockets is not stopped by
this, so we check each new subject for outbound calls.</p>
<h2>Each library runs with its documented setup</h2>
<p>Each library is set up the way its documentation shows, in
<code>subjects/&lt;name&gt;/instrument.py</code>. If that is not the setup you document, the result
is not fair, and we want to know.</p>
<h2>How to dispute a finding</h2>
<p>Open an issue with the <a href="{REPO}/issues/new?template=dispute.yml">Dispute a finding</a>
form. Every dispute is decided in public, in that issue, on one question: does the cited rule say
what the check claims? If it does not, we fix the check, and the cell changes for every library.
If the specification is unclear, the cell is marked disputed in
<a href="{REPO}/blob/main/known_issues.toml">known_issues.toml</a>, and we ask the specification's
owners.</p>
<p>Cells marked disputed today: {disputed}.</p>
<p>The project publishes no ranking and no total score, only each finding with its evidence. See
<a href="{REPO}/blob/main/GOVERNANCE.md">GOVERNANCE.md</a>.</p>
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
<link rel="alternate" type="application/atom+xml" href="feed.xml" title="spantruth changes">
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
<a href="bugs.html">the ten bug classes</a> ·
<a href="changes.html">what new releases changed</a> ·
<a href="fixes.html">fixed upstream, with credit</a>. A fix that ships upstream turns its cell
green on the latest-releases page first.</p>
<div class="legend">{legend}</div>
{_matrix(doc, names, first)}
<p class="muted">The controls run the suite's own reference instrumentation.
<code>control-honest</code> has to pass every check, and <code>control-faulty</code> has to fail
exactly its planted faults. If either moves, the suite is wrong, not a library. CI checks both,
so their cells are not in the findings below. <a href="method.html">How we know a check is
right</a>.</p>
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
