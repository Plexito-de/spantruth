"""The quick gate: one representative scenario per bug class and provider.

`spantruth run --quick` runs these instead of every scenario. Each bug class in `bugclasses.py` has
shapes here: the kinds of call that show that bug most often, best first. For each provider, the
first scenario of the best shape that the provider has represents the class. A new class without a
shape fails a test, so the gate stays in step with the classes, and a new scenario takes part
without a change here.
"""

from __future__ import annotations

from collections.abc import Callable

from . import bugclasses, scenarios
from .model import Scenario, Truth

Shape = Callable[[Scenario, Truth], bool]


def _early_exit(sc: Scenario, t: Truth) -> bool:
    return sc.read_events is not None


def _plain(sc: Scenario, t: Truth) -> bool:
    return not sc.stream and t.input_tokens is not None


def _cache(sc: Scenario, t: Truth) -> bool:
    return bool(t.cache_read_tokens and t.cache_write_tokens)


def _cache_read(sc: Scenario, t: Truth) -> bool:
    # A provider with no cache write, such as Gemini, still shows cache-blind input with a read.
    return bool(t.cache_read_tokens)


def _no_usage(sc: Scenario, t: Truth) -> bool:
    return t.no_usage


def _unread(sc: Scenario, t: Truth) -> bool:
    return bool(t.unread_counts)


def _full_stream(sc: Scenario, t: Truth) -> bool:
    return sc.stream and t.output_tokens is not None


def _reasoning(sc: Scenario, t: Truth) -> bool:
    return bool(t.reasoning_tokens)


def _stream_fails(sc: Scenario, t: Truth) -> bool:
    return sc.stream and t.error is not None


def _fails(sc: Scenario, t: Truth) -> bool:
    return t.error is not None


def _retried(sc: Scenario, t: Truth) -> bool:
    return sc.retries > 0 and t.error is None


def _tools(sc: Scenario, t: Truth) -> bool:
    return sc.tools


def _helper(sc: Scenario, t: Truth) -> bool:
    return sc.with_block or sc.entrypoint != "create"


# The shapes of each class, best first.
SHAPES: dict[str, tuple[Shape, ...]] = {
    "ST01": (_early_exit,),
    "ST02": (_plain,),
    "ST03": (_cache, _cache_read),
    "ST04": (_no_usage, _unread),
    "ST05": (_full_stream,),
    "ST06": (_reasoning,),
    "ST07": (_stream_fails, _fails),
    "ST08": (_retried,),
    "ST09": (_tools,),
    "ST10": (_helper,),
}


def picks() -> dict[str, dict[str, str]]:
    """{class ID: {provider: scenario ID}}. A provider with no scenario of a shape has no entry."""
    providers = list(dict.fromkeys(sc.provider for sc in scenarios.SCENARIOS.values()))
    out: dict[str, dict[str, str]] = {}
    for bug in bugclasses.CLASSES:
        chosen: dict[str, str] = {}
        for provider in providers:
            for shape in SHAPES[bug.id]:
                sid = next(
                    (
                        sid
                        for sid, sc in scenarios.SCENARIOS.items()
                        if sc.provider == provider and shape(sc, scenarios.truth(sc))
                    ),
                    None,
                )
                if sid:
                    chosen[provider] = sid
                    break
        out[bug.id] = chosen
    return out


def scenario_ids() -> list[str]:
    """The quick selection, each scenario once, in registry order."""
    chosen = {sid for by_provider in picks().values() for sid in by_provider.values()}
    return [sid for sid in scenarios.SCENARIOS if sid in chosen]
