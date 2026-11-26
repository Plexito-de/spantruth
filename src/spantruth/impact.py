"""What a wrong count does to a cost figure, at list price, for one cell.

The figure is for the token mix of one scenario, with the formula that follows the conventions:
input tokens include cache reads and writes, so the uncached part is `input - read - write`, not
below zero. A backend with another formula shows another number. A figure for a whole application
would need that application's mix, so this module never gives one.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import bugclasses, paths

PRICES = paths.data("prices.toml")
MILLION = 1_000_000

# The fields of a provider's `usage` that this module prices, as dotted paths. Each one is a count
# with a rate in prices.toml, or a part of such a count that bills at the same rate.
PRICED: dict[str, frozenset[str]] = {
    "anthropic": frozenset(
        {
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "cache_creation.ephemeral_5m_input_tokens",
            "cache_creation.ephemeral_1h_input_tokens",
            "output_tokens_details.thinking_tokens",  # inside output_tokens
            "server_tool_use.web_fetch_requests",  # a web fetch bills through its tokens only
        }
    ),
    "openai": frozenset(
        {
            # Chat Completions
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_tokens_details.cached_tokens",
            "prompt_tokens_details.cache_write_tokens",  # GPT-5.6 and later, at `cache_write`
            # The next three are inside completion_tokens and bill at the output rate.
            "completion_tokens_details.reasoning_tokens",
            "completion_tokens_details.accepted_prediction_tokens",
            "completion_tokens_details.rejected_prediction_tokens",
            # Responses
            "input_tokens",
            "output_tokens",
            "input_tokens_details.cached_tokens",
            "output_tokens_details.reasoning_tokens",  # inside output_tokens
        }
    ),
    "gemini": frozenset(
        {
            "promptTokenCount",  # holds cachedContentTokenCount
            "candidatesTokenCount",
            "thoughtsTokenCount",  # outside candidatesTokenCount, at the output rate
            "totalTokenCount",
            "cachedContentTokenCount",
            # The per-modality parts of the counts above. Text bills at the counts' rates; audio
            # has its own input rate on some models, so it stays unpriced.
            "promptTokensDetails.TEXT",
            "cacheTokensDetails.TEXT",
            "candidatesTokensDetails.TEXT",
        }
    ),
}
# The key of the object that holds the counts, where it is not `usage`.
USAGE_KEY = {"gemini": "usageMetadata"}
# Fields that multiply every rate of a call, and the values that leave the standard rates in force.
STANDARD: dict[str, dict[str, frozenset[str]]] = {
    "anthropic": {
        "service_tier": frozenset({"standard"}),  # `priority` and `batch` have other rates
        "inference_geo": frozenset({"global"}),  # `us` costs 1.1x
        "speed": frozenset({"standard"}),  # `fast` has premium rates
    },
    "openai": {
        "service_tier": frozenset({"default"}),  # `flex`, `priority` and `scale` have other rates
    },
    "gemini": {
        "serviceTier": frozenset({"standard"}),  # `flex` and `priority` have other rates
    },
}


def _leaves(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, inner in value.items():
            yield from _leaves(inner, f"{prefix}.{key}" if prefix else str(key))
    elif (
        isinstance(value, list)
        and value
        and all(isinstance(v, Mapping) and "modality" in v for v in value)
    ):
        # Gemini's breakdown: one leaf per modality, such as `promptTokensDetails.TEXT`.
        for entry in value:
            yield f"{prefix}.{entry['modality']}", entry.get("tokenCount")
    else:
        yield prefix, value


def _bills_nothing(value: Any, standard: frozenset[str]) -> bool:
    """True for an absent value, a count of zero, an empty list and a tier at the standard rates."""
    if value is None or (isinstance(value, (list, tuple)) and not value):
        return True
    if isinstance(value, str):
        return value in standard
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def unpriced(provider: str, response: Mapping[str, Any]) -> list[str]:
    """The fields of one response that change its price in a way this module does not know.

    `response` is the object that holds `usage`: a message, a completion, a stream chunk or event.
    A count of zero and an absent value bill nothing. Any other field outside `PRICED` and
    `STANDARD` is named: a batch or priority tier, US-only inference, an Anthropic web search or
    code execution, audio tokens, or a cache write on an OpenAI model that bills one. A cost
    figure for such a call would be wrong, so tests/test_wire.py fails on a fixture that sends
    one, and the scenario that adds it decides its price first.

    Only `usage` and the tier are read. A charge that neither one carries is not named: an OpenAI
    tool call that bills per call is an item of the output, and the higher rates of a long prompt
    follow from the input count.
    """
    if provider not in PRICED:
        raise ValueError(
            f"no price rules for provider {provider!r}: add it to impact.PRICED and impact.STANDARD"
        )
    usage = usage_of(provider, response)
    if not isinstance(usage, Mapping):
        return []  # Nothing is billed here, so a tier on this object says nothing yet.
    standard = STANDARD[provider]
    # OpenAI names the tier next to `usage`, Anthropic inside it.
    fields = [*_leaves(usage), *((k, response.get(k)) for k in standard if k not in usage)]
    return sorted(
        f"{path}={value!r}"
        for path, value in fields
        if path not in PRICED[provider]
        and not _bills_nothing(value, standard.get(path, frozenset()))
    )


def usage_of(provider: str, response: Mapping[str, Any]) -> Any:
    """The counts of one response: `usage`, or Gemini's `usageMetadata`."""
    return response.get(USAGE_KEY.get(provider, "usage"))


@dataclass(frozen=True)
class Price:
    source: str
    input: float
    cache_read: float
    cache_write: float  # a write the cache keeps for its default lifetime: 5 minutes at Anthropic
    cache_write_long: float  # a write the cache keeps for the long lifetime: 1 hour at Anthropic
    output: float


@dataclass(frozen=True)
class CellCost:
    real: float | None  # None: the wire does not carry enough to say what the call cost.
    reported: float
    percent: float


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    return tomllib.loads(PRICES.read_text())


def load_prices() -> dict[str, Price]:
    return {model: Price(**row) for model, row in _table()["models"].items()}


def prices_as_of() -> str:
    return _table()["as_of"]


def _number(value: Any) -> int | float:
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return value if is_number else 0


def _cost(
    price: Price, input_: float, output: float, read: float, write: float, long_share: float
) -> float:
    """`long_share` is the part of the cache writes, from 0 to 1, with the long lifetime."""
    uncached = max(input_ - read - write, 0)
    long_write = write * long_share
    return (
        uncached * price.input
        + read * price.cache_read
        + (write - long_write) * price.cache_write
        + long_write * price.cache_write_long
        + output * price.output
    ) / MILLION


def _long_share(truth: Mapping[str, Any]) -> float:
    """The part of the cache writes on the wire with the long lifetime, from 0 to 1.

    No span attribute separates a 1-hour write from a 5-minute one. So the reported figure gives
    the writes on the span the same share, and a correct count shows the real cost. This is an
    assumption for a wrong count: a span that reports one part alone is priced at the mix of both.
    A truth with a long part above the total is not a real response, and counts as all long.
    """
    write = truth.get("cache_write_tokens") or 0
    return min((truth.get("cache_write_long_tokens") or 0) / write, 1.0) if write else 0.0


def cell_cost(
    truth: Mapping[str, Any] | None, checks: Iterable[Mapping[str, Any]]
) -> CellCost | None:
    """The cost of one call on the wire and on the spans, or None when no figure can be given."""
    if not truth:
        return None
    price = load_prices().get(truth.get("request_model", ""))
    if price is None:
        return None
    # A refused call bills nothing, so there is no cost to lose.
    if str(truth.get("error") or "").startswith("HTTP "):
        return None
    by_id = {c["id"]: c for c in checks}
    known = truth.get("input_tokens") is not None and truth.get("output_tokens") is not None
    long_share = _long_share(truth)
    real = (
        _cost(
            price,
            truth["input_tokens"],
            truth["output_tokens"],
            truth.get("cache_read_tokens") or 0,
            truth.get("cache_write_tokens") or 0,
            long_share,
        )
        if known
        else None
    )
    ghost = any(
        (c := by_id.get(check)) and c["verdict"] in ("lost", "missing") for check in GHOST_CHECKS
    )
    if ghost:
        return CellCost(real, 0.0, -100.0)
    if real is None or real == 0:
        return None

    def reported(fact: str) -> float:
        check = by_id.get(fact)
        if check is None or check["verdict"] == "n/a":
            return truth.get(fact) or 0
        return 0 if check["verdict"] == "missing" else _number(check.get("got"))

    spans = by_id.get(COPIES_CHECK)
    copies = _number(spans.get("got")) if spans and spans["verdict"] == "wrong" else 1
    shown = _cost(
        price,
        reported("input_tokens"),
        reported("output_tokens"),
        reported("cache_read_tokens"),
        reported("cache_write_tokens"),
        long_share,
    ) * max(copies, 1)
    return CellCost(real, shown, round((shown - real) / real * 100, 1))


GHOST_CHECKS = ("span.ended", "span.recorded")
COPIES_CHECK = "tokens.single-report"


def class_cost(
    truth: Mapping[str, Any] | None, checks: Iterable[Mapping[str, Any]], class_id: str
) -> CellCost | None:
    """The figure that one bug class causes alone, with the other classes of the cell set aside."""
    if class_id not in bugclasses.COST_CLASSES:
        return None
    checks = list(checks)
    if class_id == "ST01":
        kept = [c for c in checks if c["id"] in GHOST_CHECKS]
    elif class_id == "ST02":
        kept = [c for c in checks if c["id"] == COPIES_CHECK]
    else:
        kept = [c for c in checks if c["id"] not in (*GHOST_CHECKS, COPIES_CHECK)]
    return cell_cost(truth, kept)
