"""Grade one cell: what the app saw, and what the telemetry says, against the wire truth.

Pure functions over plain records, so every verdict is testable without a subprocess.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Any

from .calls import PROMPT
from .conventions import (
    LLM_MARKERS,
    OUTPUT_EVENTS,
    TOKEN_METRIC_NAMES,
    TOKEN_METRICS,
    defined_for,
    langfuse_only,
    leaves,
    lookup,
    openinference_only,
    output_texts,
    same_finish,
    text_of,
)
from .model import Scenario, Truth
from .receiver import LogRecordData, MetricPoint, SpanRecord
from .rules import RULES

# Ordered by severity. The cell takes the most severe verdict of its checks.
SEVERITY = ("n/a", "pass", "missing", "wrong", "lost", "broken", "inconclusive")
# A failing check is any check that did not pass and applies, `inconclusive` included.
FAILING = SEVERITY[2:]
TOKENS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
VALUES = ("request_model", "response_model", "response_id", "finish_reasons", *TOKENS)


@dataclass(frozen=True)
class Check:
    id: str
    verdict: str
    expected: Any = None
    got: Any = None
    where: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        rule, url = RULES.get(self.id, ("", ""))
        return asdict(self) | {"rule": rule, "rule_url": url}


def cell_verdict(checks: list[Check]) -> str:
    return max((c.verdict for c in checks), key=SEVERITY.index, default="n/a")


def _app(base: dict, obs: dict) -> Check:
    if obs.get("hang"):
        return Check("app.unchanged", "broken", detail="the app hung until the watchdog fired")
    diffs = []
    if obs.get("exception") != base.get("exception"):
        diffs.append(f"exception {base.get('exception')} became {obs.get('exception')}")
    for key in ("text", "events", "response_id", "usage"):
        if obs["seen"].get(key) != base["seen"].get(key):
            diffs.append(f"{key} {base['seen'].get(key)!r} became {obs['seen'].get(key)!r}")
    return Check("app.unchanged", "broken" if diffs else "pass", detail="; ".join(diffs))


def _one_part(t: Truth) -> dict[int, str]:
    """The cache writes a library reports when it reads one part of the TTL breakdown alone.

    The words are Anthropic's lifetimes, 5 minutes and 1 hour. Anthropic is the only provider whose
    truth has a long part so far, so a second one needs its own words here.
    """
    write, long_write = t.cache_write_tokens or 0, t.cache_write_long_tokens or 0
    short_write = write - long_write
    if not long_write or not short_write:
        return {}
    if long_write == short_write:
        return {long_write: "one part of the cache writes left out"}
    return {
        short_write: "1-hour cache writes left out",
        long_write: "5-minute cache writes left out",
    }


def _formula(fact: str, got: int, t: Truth) -> str:
    """Name the arithmetic a wrong token count most likely came from."""
    read, write = t.cache_read_tokens or 0, t.cache_write_tokens or 0
    # With a TTL breakdown on the wire, a library can also add its parts to the total.
    breakdown = t.cache_write_long_tokens is not None
    if fact == "input_tokens" and t.input_tokens is not None:
        total = t.input_tokens
        guesses = {
            total - read - write: "cache reads and writes left out",
            total - write: "cache writes left out",
            total - read: "cache reads left out",
            total + read + write: "cache tokens counted twice",
            2 * total: "counted twice",
        }
        for reported, left_out in _one_part(t).items():
            guesses.setdefault(total - write + reported, left_out)
        if breakdown:
            guesses.setdefault(total + write, "cache writes counted twice")
        if got in guesses and read + write:
            return guesses[got]
    if fact == "cache_write_tokens" and breakdown and write:
        guesses = _one_part(t) | {2 * write: "cache writes counted twice"}
        if got in guesses:
            return guesses[got]
    if fact == "output_tokens" and t.output_tokens is not None and t.reasoning_tokens:
        if got == t.output_tokens - t.reasoning_tokens:
            return "reasoning tokens left out of output"
        if got == t.output_tokens + t.reasoning_tokens:
            return "reasoning tokens added twice"
    for other in TOKENS:
        if other != fact and getattr(t, other) == got:
            return f"equals {other.replace('_', ' ')}"
    return ""


def _json_list(value: Any) -> list | None:
    """The list inside a JSON string, for a library that encodes an array attribute as text."""
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def _value(fact: str, expected: Any, found: list[tuple[SpanRecord, str, Any]], t: Truth) -> Check:
    if expected is None:
        for _span, name, v in found:
            if fact not in TOKENS or not v:
                continue
            if t.no_usage:
                return Check(fact, "wrong", None, v, name, "estimated: the stream carried no usage")
            if v in t.unread_counts:
                return Check(
                    fact,
                    "wrong",
                    None,
                    v,
                    name,
                    "only the unread part of the stream carries this count",
                )
        return Check(fact, "n/a")
    if not found:
        return Check(fact, "missing", expected)
    for _span, name, v in found:
        ok = (
            same_finish(expected, v)
            if fact == "finish_reasons"
            else (isinstance(v, (int, float)) and v == expected)
            if fact in TOKENS
            else v == expected
        )
        if (
            not ok
            and fact == "response_model"
            and name in {"llm.model_name", "langfuse.observation.model.name"}
        ):
            ok = v == t.request_model  # this name does not separate request and response model
        if not ok:
            detail = _formula(fact, v, t) if fact in TOKENS and isinstance(v, int) else ""
            if fact == "finish_reasons" and same_finish(expected, _json_list(v)):
                detail = (
                    f"the right values, recorded as the JSON string {v} instead of a string array"
                )
            return Check(fact, "wrong", expected, v, name, detail)
    return Check(fact, "pass", expected, found[0][2], found[0][1])


# A piece of exported text this long that is part of the completion is a recorded chunk of it.
CHUNK = 8


def _records(spans: list[SpanRecord], logs: list[LogRecordData]):
    """(where, attributes, body) for every span, span event and log record of the cell."""
    for s in spans:
        yield f"span {s.name}", {"<name>": s.name, **s.attributes}, None
        for name, attrs in zip(s.events, s.event_attributes, strict=False):
            yield f"span {s.name} event {name}", attrs, None
    for rec in logs:
        yield f"log record {rec.event_name or rec.scope}", rec.attributes, rec.body


def _leak(text: str, needles: tuple[str, ...], completion: str) -> bool:
    if any(n and n in text for n in needles):
        return True
    return len(text.strip()) >= CHUNK and text in completion


def _content_off(spans, logs, completion: str, switch: str | None) -> Check:
    check = "content.off_respected"
    if switch is None:
        return Check(check, "n/a", detail="the library documents no switch that turns content off")
    needles = (*(m["content"] for m in PROMPT), completion)
    for where, attrs, body in _records(spans, logs):
        for name, v in [*attrs.items(), ("<body>", body)]:
            hit = next((leaf for leaf in leaves(v) if _leak(leaf, needles, completion)), None)
            if hit is not None:
                place = where if name == "<name>" else f"{where}: {name}"
                return Check(
                    check,
                    "wrong",
                    None,
                    hit[:200],
                    place,
                    f"recorded with content switched off: {switch}",
                )
    return Check(check, "pass", detail=f"content switched off: {switch}")


def _content_on(spans, logs, completion: str, switch: str | None) -> Check:
    check = "content.output_matches"
    if switch is None:
        return Check(check, "n/a", detail="the library documents no switch that turns content on")
    found = []
    for where, attrs, body in _records(spans, logs):
        found += [(f"{where}: {name}", text) for name, text in output_texts(attrs)]
        event = where.removeprefix("log record ")
        if event in OUTPUT_EVENTS and (text := text_of(body)) is not None:
            found.append((f"{where}: <body>", text))
    if not found:
        return Check(
            check, "missing", completion, detail=f"no completion text with content on: {switch}"
        )
    for where, text in found:
        if text != completion:
            return Check(check, "wrong", completion, text[:200], where)
    return Check(check, "pass", completion, found[0][1], found[0][0])


def _latest(points: list[MetricPoint]) -> list[MetricPoint]:
    """Each cumulative stream once, at its last export. Delta points each count once."""
    streams: dict[tuple, MetricPoint] = {}
    for i, p in enumerate(points):
        key = (
            (p.name, p.scope, p.start_ns, json.dumps(p.attributes, sort_keys=True, default=str))
            if p.temporality == 2
            else (i,)
        )
        if key not in streams or p.time_ns >= streams[key].time_ns:
            streams[key] = p
    return list(streams.values())


def _metric(fact: str, t: Truth, points: list[MetricPoint], documented: bool) -> Check:
    """The token count one call recorded on the usage metrics, against the wire.

    Only a library that documents token metrics is graded. One that does not never claimed them.
    """
    check = f"metric.{fact}"
    if not documented:
        return Check(check, "n/a", detail="the library documents no token metrics")
    found, recordings = [], {}
    for name, token_type in TOKEN_METRICS[fact]:
        mine = [
            p
            for p in points
            if p.name == name
            and (token_type is None or p.attributes.get("gen_ai.token.type") == token_type)
        ]
        if mine:
            found.append((None, name, sum(p.value for p in mine)))
            if all(p.count is not None for p in mine):
                recordings[name] = sum(p.count for p in mine)
    if not found:
        if getattr(t, fact) is None:
            return Check(check, "n/a")
        if not any(p.name in TOKEN_METRIC_NAMES for p in points):
            return Check(check, "missing", getattr(t, fact), detail="no token usage metric arrived")
        return Check(check, "missing", getattr(t, fact), detail="no metric for this count")
    graded = _value(fact, getattr(t, fact), found, t)
    twice = {name: n for name, n in recordings.items() if n > 1}
    if graded.verdict == "pass" and twice:
        name, n = next(iter(twice.items()))
        return Check(check, "wrong", 1, n, name, f"recorded {n} times for one call")
    return replace(graded, id=check)


def grade(
    sc: Scenario,
    t: Truth,
    base: dict | None,
    obs: dict | None,
    spans: list[SpanRecord],
    logs: list[LogRecordData] = (),
    content_switch: str | None = None,
    metrics: list[MetricPoint] = (),
    metrics_docs: str | None = None,
) -> list[Check]:
    """Every check of one cell: the span checks, the token usage metrics, and content capture.

    `content_switch` names the library's switch for the scenario's content mode, or None when the
    subject declares no such switch. `metrics_docs` links the library's documentation of its token
    metrics, or is None when the subject declares none.
    """
    checks = _grade(sc, t, base, obs, spans)
    if t.error is None and any(c.id == "span.parented" for c in checks):
        # A failed call, or no span to grade: the metrics are not graded either.
        points = _latest(list(metrics))
        checks += [_metric(fact, t, points, metrics_docs is not None) for fact in TOKEN_METRICS]
    if not sc.content or any(c.id in ("harness", "subject.instrument") for c in checks):
        return checks
    if obs.get("hang"):
        return checks
    own = [s for s in spans if s.span_id != obs["parent"]["span_id"]]
    completion = obs["seen"]["text"]
    if sc.content == "off":
        checks.append(_content_off(own, list(logs), completion, content_switch))
    else:
        checks.append(_content_on(own, list(logs), completion, content_switch))
    return checks


def _grade(
    sc: Scenario, t: Truth, base: dict | None, obs: dict | None, spans: list[SpanRecord]
) -> list[Check]:
    if obs is None or base is None:
        return [Check("harness", "inconclusive", detail="no observation was written")]
    if "instrument_error" in obs:
        # The subject's setup raised. That is a failing check of the subject, not a gap in the run.
        return [Check("subject.instrument", "inconclusive", detail=obs["instrument_error"])]
    checks = [_app(base, obs)]
    if obs.get("hang"):
        return checks
    parent = obs["parent"]
    llm = [s for s in spans if s.span_id != parent["span_id"] and LLM_MARKERS & s.attributes.keys()]
    open_spans = [
        r["name"]
        for sid, r in obs.get("probe", {}).items()
        if r["end_ns"] is None and sid != parent["span_id"]
    ]
    if open_spans:
        checks.append(Check("span.ended", "lost", detail=f"never ended: {', '.join(open_spans)}"))
    if not llm:
        if not open_spans:
            checks.append(Check("span.recorded", "missing", detail="no LLM span arrived"))
        return checks
    orphans = [s.name for s in llm if s.trace_id != parent["trace_id"]]
    checks.append(
        Check(
            "span.parented",
            "wrong" if orphans else "pass",
            detail=f"outside the app's trace: {', '.join(orphans)}" if orphans else "",
        )
    )
    if t.error is not None:
        errored = [s for s in llm if s.status == "ERROR"]
        checks.append(
            Check(
                "error.status",
                "pass" if errored else "wrong",
                "ERROR",
                sorted({s.status for s in llm}),
            )
        )
        typed = [s.attributes["error.type"] for s in llm if s.attributes.get("error.type")]
        if typed:
            checks.append(Check("error.type", "pass", got=typed[0]))
        elif all(openinference_only(s.attributes) for s in llm):
            checks.append(
                Check(
                    "error.type",
                    "n/a",
                    detail="OpenInference records errors as span status and events",
                )
            )
        elif all(langfuse_only(s.attributes) for s in llm):
            checks.append(
                Check(
                    "error.type",
                    "n/a",
                    detail="Langfuse records errors as span status and level",
                )
            )
        else:
            checks.append(Check("error.type", "missing"))
        return checks
    if errored := [s for s in llm if s.status == "ERROR"]:
        checks.append(
            Check(
                "status.unset",
                "wrong",
                "UNSET",
                "ERROR",
                detail=f"{len(errored)} span(s) set status to Error on a successful operation",
            )
        )
    for fact in VALUES:
        if not any(defined_for(s.attributes, fact) for s in llm):
            checks.append(
                Check(fact, "n/a", detail="not defined by the conventions this library follows")
            )
            continue
        found = [(s, *hit) for s in llm if (hit := lookup(s.attributes, fact))]
        checks.append(_value(fact, getattr(t, fact), found, t))
    reporting = [
        s.name
        for s in llm
        if lookup(s.attributes, "input_tokens") or lookup(s.attributes, "output_tokens")
    ]
    if len(reporting) > 1:
        checks.append(
            Check(
                "tokens.single-report",
                "wrong",
                1,
                len(reporting),
                detail=f"usage on {len(reporting)} spans: {', '.join(reporting)}",
            )
        )
    return checks
