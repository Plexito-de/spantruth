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
    cache_write: float
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


def _cost(price: Price, input_: float, output: float, read: float, write: float) -> float:
    uncached = max(input_ - read - write, 0)
    return (
        uncached * price.input
        + read * price.cache_read
        + write * price.cache_write
        + output * price.output
    ) / MILLION


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
    real = (
        _cost(
            price,
            truth["input_tokens"],
            truth["output_tokens"],
            truth.get("cache_read_tokens") or 0,
            truth.get("cache_write_tokens") or 0,
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
