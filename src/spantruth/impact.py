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
