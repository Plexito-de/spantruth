"""The lock-file checker page: its parsers agree with doctor's, and it cannot leak or inject."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from spantruth import doctor, report, site

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "docs" / "site"
LOCKS = REPO / "tests" / "js" / "locks"
NODE = shutil.which("node")


def node(script: str) -> str:
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("CI has no node, so the checker's JavaScript tests cannot run")
        pytest.skip("node is not installed")
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_the_javascript_tests_pass():
    if NODE is None and not os.environ.get("CI"):
        pytest.skip("node is not installed")
    proc = subprocess.run(
        [NODE or "node", "--test", "tests/js/check.test.mjs"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(r"^# fail 0$", proc.stdout, re.M), proc.stdout


@pytest.mark.parametrize("name", sorted(p.name for p in LOCKS.iterdir()))
def test_the_page_reads_each_lock_file_as_doctor_does(name, tmp_path):
    shutil.copy(LOCKS / name, tmp_path / name)
    python = doctor.read_versions(tmp_path).versions
    script = (
        f"import {{ parseLock }} from {json.dumps((PAGE / 'check.js').as_uri())};"
        f"import {{ readFileSync }} from 'node:fs';"
        f"const text = readFileSync({json.dumps(str(LOCKS / name))}, 'utf8');"
        "console.log(JSON.stringify(Object.fromEntries(parseLock(text).versions)));"
    )
    assert json.loads(node(script)) == python


def test_a_finding_anchor_is_the_same_in_both_languages():
    cases = [("open inference", "a/b/c", "span.ended"), ("x<y>", "openai/basic", "cost $")]
    script = (
        f"import {{ findingId }} from {json.dumps((PAGE / 'check.js').as_uri())};"
        f"for (const c of {json.dumps(cases)}) console.log(findingId(...c));"
    )
    assert node(script).split() == [report.finding_id(*c) for c in cases]


def _tokens(css: str) -> list[str]:
    return re.findall(r"--[a-z-]+:#[0-9a-f]{6}", css)


def test_the_page_uses_the_reports_colours():
    css = (PAGE / "check.css").read_text()
    assert _tokens(css) == _tokens(site.CSS)


def test_the_page_writes_text_only_and_requests_nothing_but_the_table():
    js = (PAGE / "check.js").read_text()
    for sink in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "Function(",
    ):
        assert sink not in js, sink
    assert re.findall(r"fetch\(([^,)]*)", js) == ['"versions.json"']
    for call in ("XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "import("):
        assert call not in js, call


def test_the_page_has_a_strict_policy_and_no_third_party_asset():
    html = (PAGE / "check.html").read_text()
    policy = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html)[1]
    rules = dict(r.strip().split(" ", 1) for r in policy.split(";"))
    assert rules["default-src"] == "'none'"
    assert rules["script-src"] == "'self'"
    assert rules["connect-src"] == "'self'"
    assert rules["form-action"] == "'none'"
    assert rules["require-trusted-types-for"] == "'script'"
    assert re.findall(r'<script[^>]*src="([^"]+)"', html) == ["check.js"]
    assert "<style" not in html and " style=" not in html  # style-src 'self' allows no inline style
    assert "nothing" in html.lower() or "sends it nowhere" in html
