"""The Markdown and HTML renderers, on a small hand-built results document."""

from spantruth import expectations, report

HOSTILE = "<script>alert(1)</script>"


def doc():
    check = {
        "id": "output_tokens",
        "verdict": "wrong",
        "expected": 419,
        "got": 420,
        "where": "gen_ai.usage.output_tokens",
        "detail": HOSTILE,
        "rule": "r",
        "rule_url": "https://example.com/rule",
    }
    return {
        "spantruth": "0.1.0",
        "mode": "pinned",
        "created": "2026-10-10T00:00:00+00:00",
        "python": "3.12",
        "semconv_genai": "6fd0d763",
        "scenarios": {
            "anthropic/stream": {"title": "Stream", "catches": "", "provider": "anthropic"}
        },
        "subjects": {
            "lib": {
                "title": HOSTILE,
                "homepage": "https://example.com",
                "kind": "library",
                "versions": {"lib": "1.0"},
                "cells": {"anthropic/stream": {"verdict": "wrong", "checks": [check]}},
            },
            "control-honest": {
                "title": "c",
                "homepage": "https://example.com",
                "kind": "control",
                "cells": {"anthropic/stream": {"verdict": "pass", "checks": []}},
            },
        },
    }


def test_known_issue_is_linked_from_its_finding():
    known = [
        {
            "subject": "lib",
            "scenarios": ["anthropic/stream"],
            "check": "output_tokens",
            "status": "reported",
            "url": "https://example.com/issues/1",
            "note": "",
        }
    ]
    (f,) = report.findings(doc(), known)
    assert f["issue"]["url"] == "https://example.com/issues/1"
    assert report.findings(doc(), [])[0]["issue"] is None


def test_markdown_lists_libraries_before_controls_and_shows_the_finding():
    md = report.markdown(doc())
    assert "| Scenario | lib | control-honest |" in md
    assert "| lib | `anthropic/stream` | output_tokens | " in md
    assert "| wrong | `419` | `420` |" in md


def test_html_escapes_every_value_from_the_results():
    page = report.html(doc())
    assert HOSTILE not in page
    assert "&lt;script&gt;" in page
    assert 'href="https://example.com/rule"' in page


def test_html_has_no_scripts_and_no_external_assets():
    page = report.html(doc())
    assert "<script" not in page.replace("&lt;script", "")
    assert "src=" not in page


def test_badges_count_graded_cells_and_skip_controls():
    d = doc()
    d["subjects"]["lib"]["cells"]["openai/basic"] = {"verdict": "pass", "checks": []}
    d["subjects"]["lib"]["cells"]["openai/stream"] = {"verdict": "inconclusive", "checks": []}
    badges = report.badges(d)
    assert set(badges) == {"lib"}
    assert badges["lib"] == {
        "schemaVersion": 1,
        "label": "spantruth",
        "message": "1/2 pass",
        "color": "orange",
    }
    d["subjects"]["lib"]["cells"]["anthropic/stream"]["verdict"] = "pass"
    assert report.badges(d)["lib"]["color"] == "brightgreen"


def doc_with_spans():
    d = doc()
    d["subjects"]["lib"]["notes"] = f"runs older SDKs {HOSTILE}"
    d["subjects"]["lib"]["cells"]["anthropic/stream"]["spans"] = [
        {
            "name": f"chat {HOSTILE}",
            "scope": "lib.instrumentation",
            "status": "OK",
            "attributes": {"gen_ai.usage.output_tokens": 420, HOSTILE: f'"><img src=x>{HOSTILE}'},
        }
    ]
    return d


def test_a_finding_anchor_is_stable_and_unique():
    a = report.finding_id("lib", "anthropic/stream", "output_tokens")
    assert a == report.finding_id("lib", "anthropic/stream", "output_tokens")
    assert a != report.finding_id("lib", "anthropic/stream", "input_tokens")
    assert a != report.finding_id("lib", "anthropic/async-stream", "output_tokens")
    assert report.finding_id("my lib<x>", "openai/basic", "a.b") == "my-lib-x---openai--basic--a.b"


def test_a_finding_carries_its_anchor_and_the_command_that_reproduces_it():
    (f,) = report.findings(doc(), [])
    assert f["anchor"] == report.finding_id("lib", "anthropic/stream", "output_tokens")
    assert f["reproduce"] == "spantruth run --subject lib --scenario anthropic/stream"


def test_the_matrix_cell_links_to_its_finding():
    page = report.html(doc())
    anchor = report.finding_id("lib", "anthropic/stream", "output_tokens")
    assert f'href="#{anchor}"' in page and f'id="{anchor}"' in page


def test_a_finding_shows_the_exported_spans_and_escapes_them():
    page = report.html(doc_with_spans())
    assert "gen_ai.usage.output_tokens" in page and "lib.instrumentation" in page
    assert "<img" not in page and HOSTILE not in page
    assert "runs older SDKs" in page


def test_a_markdown_value_cannot_break_the_table():
    d = doc()
    check = d["subjects"]["lib"]["cells"]["anthropic/stream"]["checks"][0]
    check["got"], check["where"] = "a|b`c", "x|y"
    (row,) = [line for line in report.markdown(d).splitlines() if line.startswith("| lib |")]
    assert row.count("|") - row.count("\\|") == 11


def test_a_script_address_is_never_a_link():
    d = doc()
    d["subjects"]["lib"]["homepage"] = "javascript:alert(1)"
    d["subjects"]["lib"]["cells"]["anthropic/stream"]["checks"][0]["rule_url"] = "javascript:x()"
    assert 'href="javascript' not in report.html(d)


def test_a_markdown_detail_or_error_cannot_carry_markup():
    d = doc()
    check = d["subjects"]["lib"]["cells"]["anthropic/stream"]["checks"][0]
    check["detail"] = "![pixel](https://example.com/p.png) ``two``"
    d["subjects"]["lib"]["error"] = "Traceback ```\n<img src=x>"
    md = report.markdown(d)
    row = next(line for line in md.splitlines() if line.startswith("| lib |"))
    assert "``` ![pixel](https://example.com/p.png) ``two`` ```" in row
    assert "````\nTraceback ```\n<img src=x>\n````" in md


def test_a_directory_subject_is_reproduced_by_its_path():
    d = doc()
    d["subjects"]["lib"]["ref"] = "./my subject"
    (f,) = report.findings(d, [])
    assert f["reproduce"] == "spantruth run --subject './my subject' --scenario anthropic/stream"


def test_known_issues_only_annotate_built_in_subjects():
    known = [
        {
            "subject": "lib",
            "scenarios": ["anthropic/stream"],
            "check": "output_tokens",
            "status": "reported",
            "url": "https://example.com/issues/1",
            "note": "",
        }
    ]
    d = doc()
    d["subjects"]["lib"]["ref"] = "./lib"
    assert report.findings(d, known)[0]["issue"] is None


def priced_doc():
    d = doc()
    d["scenarios"]["anthropic/stream"]["truth"] = {
        "request_model": "claude-sonnet-5-5",
        "input_tokens": 1000,
        "output_tokens": 419,
        "error": None,
    }
    return d


def test_a_finding_names_its_bug_class():
    (found,) = [f for f in report.findings(doc()) if f["id"] == "output_tokens"]
    assert found["bug_class"].name == "Phantom tokens"


def test_a_finding_shows_what_it_does_to_the_cost():
    page = report.html(priced_doc())
    assert "bugs.html#ST04" in page
    assert "Phantom tokens" in page
    assert "<dt>Cost</dt>" in page
    assert "at list price" in page


def test_a_document_without_truth_renders_with_no_cost():
    assert "<dt>Cost</dt>" not in report.html(doc())


def test_markdown_names_the_bug_class():
    assert "ST04 Phantom tokens" in report.markdown(doc())


def test_the_bug_page_lists_every_class_with_its_anchor_and_escapes_values():
    page = report.bugs_html(doc())
    assert "<script>" not in page
    for n in range(1, 11):
        assert f'id="ST{n:02}"' in page
    assert "uvx spantruth doctor" in page


def test_the_bug_page_counts_cells_and_libraries_and_never_names_a_ranking():
    page = report.bugs_html(doc())
    assert "1 cell in 1 library" in page
    assert "rank" not in page.lower()


def test_the_bug_page_leaves_the_controls_out():
    d = doc()
    d["subjects"]["lib"]["kind"] = "control"
    assert "cell in" not in report.bugs_html(d).split('id="ST04"')[1].split("</section>")[0]


def test_the_bug_page_credits_who_reported_each_public_instance():
    assert "arthi-arumugam-git" in report.bugs_html(doc())


def test_comment_starts_with_a_marker_that_no_subject_name_can_break():
    text = report.comment(doc(), "lib")
    assert text.startswith("<!-- spantruth:lib -->\n")
    assert report.comment_marker("a-->b c") == "<!-- spantruth:a___b_c -->"


def test_comment_without_an_expectation_file_counts_cells_and_names_classes():
    text = report.comment(doc(), "lib")
    assert "0 of 1 cells pass" in text
    assert "ST04" in text


def test_comment_with_no_difference_says_so():
    assert "No change" in report.comment(doc(), "lib", differences=[])


def test_comment_lists_each_difference_with_its_bug_class():
    new = expectations.Difference("anthropic/stream", "output_tokens", None, "wrong")
    gone = expectations.Difference("anthropic/stream", "span.ended", "lost", None)
    text = report.comment(doc(), "lib", differences=[new, gone])
    assert "2 checks changed" in text
    assert "newly failing: wrong" in text
    assert "now passes, was lost" in text
    assert "ST01 Ghost span" in text


def test_comment_leaves_out_what_a_library_can_control():
    d = doc()
    d["subjects"]["lib"]["title"] = "[y](javascript:alert(1))"
    d["subjects"]["lib"]["cells"]["anthropic/stream"]["checks"][0]["detail"] = "[z](https://x.test)"
    text = report.comment(d, "lib")
    assert "javascript" not in text
    assert "x.test" not in text


def test_comment_keeps_a_subject_name_as_code():
    assert "`lib`" in report.comment(doc(), "lib")


def test_comment_links_the_run_only_when_it_is_a_web_address():
    assert "https://example.com/run" in report.comment(doc(), "lib", link="https://example.com/run")
    assert "javascript" not in report.comment(doc(), "lib", link="javascript:alert(1)")


def test_comment_says_when_the_run_is_incomplete():
    d = doc()
    d["subjects"]["lib"]["error"] = "environment build failed"
    assert "could not run" in report.comment(d, "lib", differences=[])
