"""The site's pages beside the report: upstream fixes."""

import json

from test_report import doc

from spantruth import cli, report, site

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
