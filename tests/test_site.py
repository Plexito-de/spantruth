"""The site's pages beside the report: upstream fixes, one page per bug class, the sitemap."""

import json
import re

from test_report import doc

from spantruth import bugclasses, cli, expectations, report, rules, site, subjects

HOSTILE = "<script>alert(1)</script>"


def fixed(**extra):
    return {
        "subject": "lib",
        "scenarios": ["anthropic/stream-early-exit"],
        "check": "span.ended",
        "status": "fixed-upstream",
        "url": "https://github.com/acme/lib/issues/12",
        "reported_by": "alice",
        "reported_on": "2026-10-04",
        "fix_url": "https://github.com/acme/lib/pull/15",
        "fixed_by": "bob",
        "fixed_on": "2026-10-12",
        "fixed_in": ["lib-anthropic>=3.1.0"],
        "note": "",
        **extra,
    }


def test_the_fixes_page_credits_who_reported_and_who_fixed():
    page = site.render_fixes([fixed()])
    assert "alice" in page and "bob" in page
    assert 'href="https://github.com/acme/lib/pull/15"' in page
    assert "lib#15" in page and "lib#12" in page
    assert "lib-anthropic 3.1.0" in page
    assert "8 days" in page
    assert "Ghost span" in page


def test_the_fixes_page_lists_only_fixed_issues_newest_first():
    older = fixed(url="https://github.com/acme/lib/issues/1", fixed_on="2026-10-05")
    open_issue = {**fixed(url="https://github.com/acme/lib/issues/9"), "status": "reported"}
    page = site.render_fixes([older, open_issue, fixed()])
    assert "lib#9" not in page
    assert page.index("lib#12") < page.index("lib#1<")


def test_the_fixes_page_says_one_day_and_escapes_every_value():
    page = site.render_fixes([fixed(fixed_by=HOSTILE, fixed_on="2026-10-05", note=HOSTILE)])
    assert HOSTILE not in page
    assert "1 day" in page and "1 days" not in page


def test_the_fixes_page_has_its_own_title_canonical_and_preview():
    page = site.render_fixes([fixed()])
    assert "<title>Telemetry bugs fixed upstream, with credit</title>" in page
    assert '<link rel="canonical" href="https://plexito-de.github.io/spantruth/fixes.html">' in page
    assert 'property="og:title"' in page and 'property="og:description"' in page
    assert 'property="og:url"' in page
    assert "<script" not in page and "src=" not in page


def test_the_shipped_fixes_render():
    page = site.render_fixes(report.known_issues())
    assert "PabloPardoGarcia" in page and "satyadevai" in page


def test_the_report_and_the_bug_page_link_the_fixes_page():
    assert 'href="fixes.html"' in report.html(doc())
    assert 'href="fixes.html"' in report.bugs_html(doc())


def test_the_bugs_option_writes_the_fixes_page_beside_the_bug_page(tmp_path):
    results = tmp_path / "r.json"
    results.write_text(json.dumps(doc()))
    assert cli.main(["report", str(results), "--bugs", str(tmp_path / "site" / "bugs.html")]) == 0
    assert "fixed upstream" in (tmp_path / "site" / "fixes.html").read_text()
    assert "Streamed output tokens" in (tmp_path / "site/bugs/phantom-tokens.html").read_text()
    assert "bugs/ghost-span.html" in (tmp_path / "site" / "sitemap.xml").read_text()


def test_every_class_has_a_page_with_a_title_and_description_that_fit_a_search_result():
    assert set(bugclasses.PAGES) == set(bugclasses.BY_ID)
    for c in bugclasses.CLASSES:
        page = bugclasses.PAGES[c.id]
        assert 10 <= len(page.symptom) <= 60, page.symptom
        assert 70 <= len(site.class_description(c)) <= 160, c.id
        assert page.cause and page.fix


def test_a_class_page_names_each_library_and_version_that_shows_it():
    d = doc()
    d["subjects"]["lib"]["versions"] = {"lib-anthropic": "1.0", "x": HOSTILE}
    page = site.render_class(d, bugclasses.BY_ID["ST04"], [])
    assert "<title>Streamed output tokens too high in LLM traces</title>" in page
    assert "lib-anthropic 1.0" in page
    assert "In the run of October 10, 2026, at the pinned versions" in page
    assert HOSTILE not in page and "&lt;script&gt;" in page
    anchor = report.finding_id("lib", "anthropic/stream", "output_tokens")
    assert f'href="../index.html#{anchor}"' in page
    assert "uvx spantruth doctor" in page
    assert "rank" not in page.lower()


def test_a_class_page_says_when_the_run_did_not_show_it():
    page = site.render_class(doc(), bugclasses.BY_ID["ST08"], [])
    assert "at the pinned versions, with spantruth 0.1.0, no library shows it" in page
    assert "control-honest" not in page


def test_a_class_page_has_its_canonical_address_and_link_preview():
    page = site.render_class(doc(), bugclasses.BY_ID["ST01"], [])
    url = "https://plexito-de.github.io/spantruth/bugs/ghost-span.html"
    assert f'<link rel="canonical" href="{url}">' in page
    assert f'<meta property="og:url" content="{url}">' in page
    assert 'name="description"' in page and 'property="og:image"' in page
    assert "<script" not in page and "src=" not in page


def test_a_class_page_links_the_upstream_fixes_of_its_class():
    page = site.render_class(doc(), bugclasses.BY_ID["ST01"], report.known_issues())
    assert 'href="../fixes.html#openinference-3930"' in page
    assert "openinference-instrumentation-anthropic 3.1.0" in page


def test_the_bug_page_links_each_class_page():
    page = report.bugs_html(doc())
    for c in bugclasses.CLASSES:
        assert f'href="bugs/{bugclasses.slug(c)}.html"' in page


def test_the_sitemap_lists_every_page_by_its_full_address():
    xml = site.sitemap()
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    for path in ("index.html", "bugs.html", "fixes.html", "method.html", "bugs/false-ok.html"):
        assert f"<loc>https://plexito-de.github.io/spantruth/{path}</loc>" in xml


def test_the_method_page_explains_every_planted_fault():
    source = (subjects.SUBJECTS / "control-faulty" / "instrument.py").read_text()
    faults = re.findall(r'^\s+"([a-z-]+)",$', source, re.M)
    assert len(faults) >= 5
    assert set(faults) == set(site.FAULTS)
    expected = expectations.load(subjects.SUBJECTS / "control-faulty" / "expected.toml")
    planted = {c for found in expected.values() for c in found}
    assert planted == {c for _, checks in site.FAULTS.values() for c in checks}
    page = site.render_method(doc(), [])
    assert f"make {len(planted)} of the {len(rules.RULES)} checks fail on purpose" in page
    for fault in faults:
        assert f"<code>{fault}</code>" in page


def test_the_method_page_shows_the_rule_behind_every_check():
    page = site.render_method(doc(), [])
    for check, (_, url) in rules.RULES.items():
        assert f"<code>{check}</code>" in page
        if url:
            assert f'href="{url}"' in page


def test_the_method_page_counts_the_planted_faults_from_the_expectation_file():
    expected = expectations.load(subjects.SUBJECTS / "control-faulty" / "expected.toml")
    checks = sum(len(v) for v in expected.values())
    page = site.render_method(doc(), [])
    assert f"{checks} failing checks in {len(expected)} scenarios" in page


def _controls(honest_fails: bool = False, faulty: str = "exact") -> dict:
    d = doc()
    expected = expectations.load(subjects.SUBJECTS / "control-faulty" / "expected.toml")
    cells = {}
    for sid, checks in expected.items():
        cells[sid] = {
            "verdict": "wrong",
            "checks": [{"id": c, "verdict": v} for c, v in checks.items()],
        }
    if faulty == "moved":
        cells[next(iter(cells))] = {"verdict": "pass", "checks": []}
    d["subjects"]["control-faulty"] = {**d["subjects"]["control-honest"], "cells": cells}
    if honest_fails:
        d["subjects"]["control-honest"]["cells"]["anthropic/stream"] = {
            "verdict": "wrong",
            "checks": [{"id": "output_tokens", "verdict": "wrong"}],
        }
    return d


def test_the_method_page_reports_what_the_controls_did_in_this_run():
    page = site.render_method(_controls(), [])
    assert "control-honest passed every check in 1 scenario" in page
    assert "control-faulty failed exactly its planted faults" in page
    assert "moved" not in site.render_method(_controls(), [])
    assert "control-honest moved" in site.render_method(_controls(honest_fails=True), [])
    assert "control-faulty moved" in site.render_method(_controls(faulty="moved"), [])


def test_the_method_page_counts_disputed_cells():
    disputed = {**fixed(), "status": "disputed"}
    assert "Cells marked disputed today: 1." in site.render_method(doc(), [disputed, fixed()])
    assert "Cells marked disputed today: 0." in site.render_method(doc(), [])


def test_the_method_page_is_linked_and_written_beside_the_bug_page():
    assert 'href="method.html"' in report.html(doc())
    assert 'href="method.html"' in report.bugs_html(doc())
    page = report.bug_pages(doc())["method.html"]
    assert (
        '<link rel="canonical" href="https://plexito-de.github.io/spantruth/method.html">' in page
    )
    assert "<script" not in page and "src=" not in page
