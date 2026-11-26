"""Every Collector recipe in docs/remedies/README.md, run on a real OpenTelemetry Collector.

The page promises that each recipe was tested on a named Collector version. So the test reads the
recipes from the page itself, runs each one in otelcol-contrib, and checks what the file exporter
writes. The input spans come from what spantruth recorded: span records as a library exported
them, or a span built from the attribute values that a results document records for each check.

The Collector tests are marked `collector` and skipped when the binary is absent. The `remedies`
job in CI downloads it with docs/remedies/fetch-otelcol.sh, which checks its sha256, and sets
SPANTRUTH_OTELCOL, so that a missing binary fails there instead of skipping.
"""

from __future__ import annotations

import base64
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from google.protobuf import json_format
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from spantruth.receiver import SpanRecord, decode_traces

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "docs" / "remedies" / "README.md"
FETCH = REPO / "docs" / "remedies" / "fetch-otelcol.sh"
DOUBLE_SPANS = REPO / "results" / "2026-11-06-double-spans" / "spans-x2-phoenix.jsonl"
PINNED = REPO / "results" / "2026-11-03-pinned.json"
SDK_SCOPE = "com.anthropic.sdk.python"


def recipes() -> dict[str, str]:
    """Each fenced YAML block on the page that starts with `# recipe: <id>`, by its ID."""
    found = {}
    for block in re.findall(r"```yaml\n(.*?)```", PAGE.read_text(), re.S):
        first, _, _ = block.partition("\n")
        if first.startswith("# recipe: "):
            found[first.removeprefix("# recipe: ").strip()] = block
    return found


def pinned_version() -> str:
    return re.search(r"^VERSION=(\S+)$", FETCH.read_text(), re.M).group(1)


def collector_binary() -> Path:
    if os.environ.get("SPANTRUTH_OTELCOL"):
        return Path(os.environ["SPANTRUTH_OTELCOL"])
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "spantruth" / f"otelcol-contrib-{pinned_version()}" / "otelcol-contrib"


# --- Input spans: what spantruth recorded ------------------------------------------------------


def recorded(*projects: str) -> list[dict]:
    """The spans that each library exported in the double-span measurement, by project name."""
    lines = DOUBLE_SPANS.read_text().splitlines()
    spans = [json.loads(line) for line in lines]
    found = [s for s in spans if s["project"] in projects]
    assert {s["project"] for s in found} == set(projects)
    return found


def from_results(scenario: str, *subjects: str) -> list[dict]:
    """One span per subject, built from what the checks of one cell of a results document read.

    Each check with an attribute name (`where`) gives that attribute its value (`got`). The
    `error.status` check gives the status, and the `error.type` check the value of `error.type`.
    """
    doc = json.loads(PINNED.read_text())
    out = []
    for subject in subjects:
        checks = {c["id"]: c for c in doc["subjects"][subject]["cells"][scenario]["checks"]}
        attributes = {
            c["where"]: c["got"] for c in checks.values() if c["where"] and c["got"] is not None
        }
        if checks.get("error.type", {}).get("got"):
            attributes["error.type"] = checks["error.type"]["got"]
        status = (checks.get("error.status", {}).get("got") or ["UNSET"])[0]
        out.append(
            {
                "project": subject,
                "scope": f"spantruth.results/{subject}",
                "name": scenario,
                "trace_id": f"{len(out) + 1:032x}",
                "span_id": f"{len(out) + 1:016x}",
                "parent": None,
                "status": status,
                "attributes": attributes,
            }
        )
    return out


def any_value(v) -> common_pb2.AnyValue:
    if isinstance(v, bool):
        return common_pb2.AnyValue(bool_value=v)
    if isinstance(v, int):
        return common_pb2.AnyValue(int_value=v)
    if isinstance(v, float):
        return common_pb2.AnyValue(double_value=v)
    if isinstance(v, list):
        return common_pb2.AnyValue(
            array_value=common_pb2.ArrayValue(values=[any_value(x) for x in v])
        )
    return common_pb2.AnyValue(string_value=str(v))


def otlp(spans: list[dict]) -> bytes:
    """An OTLP export request with one resource per project and one scope per library."""
    request = trace_service_pb2.ExportTraceServiceRequest()
    status = {"UNSET": 0, "OK": 1, "ERROR": 2}
    for project in dict.fromkeys(s["project"] for s in spans):
        rs = request.resource_spans.add()
        rs.resource.attributes.add(key="service.name", value=any_value(project))
        mine = [s for s in spans if s["project"] == project]
        for scope in dict.fromkeys(s["scope"] for s in mine):
            ss = rs.scope_spans.add()
            ss.scope.name = scope
            for s in (s for s in mine if s["scope"] == scope):
                ss.spans.add(
                    trace_id=bytes.fromhex(s["trace_id"]),
                    span_id=bytes.fromhex(s["span_id"]),
                    parent_span_id=bytes.fromhex(s["parent"] or ""),
                    name=s["name"],
                    kind=trace_pb2.Span.SPAN_KIND_CLIENT,
                    start_time_unix_nano=1_762_387_200_000_000_000,
                    end_time_unix_nano=1_762_387_201_000_000_000,
                    status=trace_pb2.Status(code=status[s["status"]]),
                    attributes=[
                        common_pb2.KeyValue(key=k, value=any_value(v))
                        for k, v in s["attributes"].items()
                    ],
                )
    return request.SerializeToString()


# --- The Collector ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hex_ids_to_base64(node):
    """OTLP JSON writes IDs as hex, and protobuf's JSON parser wants base64 for bytes."""
    if isinstance(node, dict):
        return {
            k: base64.b64encode(bytes.fromhex(v)).decode()
            if k in ("traceId", "spanId", "parentSpanId")
            else _hex_ids_to_base64(v)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_hex_ids_to_base64(x) for x in node]
    return node


def run_collector(
    binary: Path, pipeline: list[str], spans: list[dict], tmp: Path
) -> list[SpanRecord]:
    """Send the spans through a Collector that runs these recipes in order, and read the output."""
    port, out = free_port(), tmp / "out.jsonl"
    processors = {}
    for recipe in pipeline:
        processors.update(yaml.safe_load(recipe)["processors"])
    config = {
        "receivers": {"otlp": {"protocols": {"http": {"endpoint": f"127.0.0.1:{port}"}}}},
        "processors": processors,
        "exporters": {"file": {"path": str(out)}},
        "service": {
            "telemetry": {"metrics": {"level": "none"}},
            "pipelines": {
                "traces": {
                    "receivers": ["otlp"],
                    "processors": list(processors),
                    "exporters": ["file"],
                }
            },
        },
    }
    (tmp / "config.yaml").write_text(yaml.safe_dump(config))
    log = (tmp / "collector.log").open("w")
    proc = subprocess.Popen(
        [str(binary), "--config", str(tmp / "config.yaml")], stdout=log, stderr=log
    )
    try:
        _post(f"http://127.0.0.1:{port}/v1/traces", otlp(spans), proc, tmp)
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
        log.close()
    records = []
    for line in out.read_text().splitlines():
        request = json_format.Parse(
            json.dumps(_hex_ids_to_base64(json.loads(line))),
            trace_service_pb2.ExportTraceServiceRequest(),
        )
        records.extend(decode_traces(request.SerializeToString()))
    return records


def _post(url: str, body: bytes, proc: subprocess.Popen, tmp: Path) -> None:
    """POST once the receiver listens. The receiver answers after the exporter took the spans."""
    no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-protobuf"}
    )
    deadline = time.monotonic() + 30
    while True:
        if proc.poll() is not None:
            pytest.fail(f"the Collector exited:\n{(tmp / 'collector.log').read_text()}")
        try:
            with no_proxy.open(request, timeout=10) as response:
                assert response.status == 200
                return
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


# --- What each recipe must do ----------------------------------------------------------------


def by_service(spans: list[SpanRecord], service: str) -> list[SpanRecord]:
    return [s for s in spans if s.resource["service.name"] == service]


def input_total(spans: list[SpanRecord]) -> int:
    return sum(
        s.attributes.get("gen_ai.usage.input_tokens", s.attributes.get("llm.token_count.prompt", 0))
        for s in spans
    )


DOUBLED = ("x2-base-A-ll", "x2-base-A-otel", "x2-base-A-oi310")


def check_st02_drop(out: list[SpanRecord]) -> None:
    assert not [s for s in out if s.scope == SDK_SCOPE]
    for project in DOUBLED:
        assert len(by_service(out, project)) == 1
        assert input_total(by_service(out, project)) == 1000
    # The caveat on the page: a call through the SDK alone loses its only span.
    assert by_service(out, "x2-base-C-sdk") == []


def check_st02_strip(out: list[SpanRecord]) -> None:
    sdk = [s for s in out if s.scope == SDK_SCOPE]
    assert len(sdk) == 4
    for s in sdk:
        assert not [k for k in s.attributes if k.startswith("gen_ai.usage.")]
        assert s.attributes["http.response.status_code"] == 200
    for project in DOUBLED:
        assert len(by_service(out, project)) == 2
        assert input_total(by_service(out, project)) == 1000
    assert input_total(by_service(out, "x2-base-C-sdk")) == 0


def check_st07(out: list[SpanRecord]) -> None:
    (library,) = by_service(out, "openllmetry")  # ERROR, and no error.type
    assert library.attributes["error.type"] == "_OTHER"
    (honest,) = by_service(out, "control-honest")  # ERROR, with its own error.type
    assert honest.attributes["error.type"] == "RemoteProtocolError"
    (faulty,) = by_service(out, "control-faulty")  # UNSET: a false OK cannot be flagged
    assert "error.type" not in faulty.attributes


Case = tuple[tuple[str, ...], Callable[[], list[dict]], Callable[[list[SpanRecord]], None]]

# Each case: the recipes of the pipeline in order, the spans it gets, and what must come out.
CASES: dict[str, Case] = {
    "st02-drop-sdk-span": (
        ("st02-drop-sdk-span",),
        lambda: recorded(*DOUBLED, "x2-base-C-sdk"),
        check_st02_drop,
    ),
    "st02-strip-sdk-usage": (
        ("st02-strip-sdk-usage",),
        lambda: recorded(*DOUBLED, "x2-base-C-sdk"),
        check_st02_strip,
    ),
    "st07-flag-missing-error-type": (
        ("st07-flag-missing-error-type",),
        lambda: from_results(
            "anthropic/async-stream-dropped", "openllmetry", "control-honest", "control-faulty"
        ),
        check_st07,
    ),
}


# --- Tests -------------------------------------------------------------------------------------


def test_every_recipe_on_the_page_has_a_case_and_every_case_a_recipe():
    assert set(recipes()) == {name for pipeline, _, _ in CASES.values() for name in pipeline}


def test_every_recipe_is_a_processors_block():
    for name, recipe in recipes().items():
        parsed = yaml.safe_load(recipe)
        assert list(parsed) == ["processors"], name
        assert len(parsed["processors"]) == 1, name


def test_the_page_names_the_version_that_the_test_downloads():
    assert f"otelcol-contrib {pinned_version()}" in PAGE.read_text()


@pytest.mark.collector
@pytest.mark.parametrize("name", sorted(CASES))
def test_recipe_in_the_collector(name, tmp_path):
    binary = collector_binary()
    # A binary that SPANTRUTH_OTELCOL names must exist: CI sets it, and a skip there tests nothing.
    if not binary.is_file() and not os.environ.get("SPANTRUTH_OTELCOL"):
        pytest.skip(f"no Collector at {binary}: run docs/remedies/fetch-otelcol.sh")
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=True
    ).stdout
    assert version.split()[-1] == pinned_version()
    pipeline, spans, check = CASES[name]
    sent = spans()
    out = run_collector(binary, [recipes()[r] for r in pipeline], sent, tmp_path)
    if "st02-drop-sdk-span" not in pipeline:
        assert len(out) == len(sent)
    check(out)
