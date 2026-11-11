"""A strategy's rules in plain words, worded from the parsed definition.

The app's draft page shows these so the operator can check a strategy against
the video or idea it came from before any search runs. They are worded from
:class:`~strategylab.core.types.StrategyDef` - what the loader actually read -
and not from the file's text or its description, so a rule the loader read
differently from how it was written shows up as written wrong.

THE WORDING COVERS THE WHOLE VOCABULARY AND NOTHING ELSE. Every indicator in
:data:`~strategylab.core.config.INDICATORS` and every operator in
:data:`~strategylab.core.config.OPERATORS` has words here, and a test asserts
that. A name added to either without words here makes that test fail, rather
than reaching the page as a raw identifier.

A day here is a trading day: one bar of the daily data the project uses.
"""

from __future__ import annotations

import math
from typing import Any

from strategylab.core.types import Condition, IndicatorRef, StrategyDef

#: Each operator as a verb phrase, between the two sides of a condition.
OPERATOR_WORDS: dict[str, str] = {
    "above": "is above",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
}

#: The one fixed parameter with a meaning of its own, and how it is worded.
POSITION_SIZE = "position_size_pct"


def _number(value: float) -> str:
    """Write a number without a trailing ``.0`` when it is whole.

    Args:
        value: The number.

    Returns:
        ``"50"`` for 50.0, ``"0.5"`` for 0.5.
    """
    return str(int(value)) if value == int(value) else f"{value:g}"


def _side(ref: IndicatorRef) -> str:
    """Word one side of a condition.

    Args:
        ref: An indicator reference.

    Returns:
        ``"the price"`` for the close, and ``"the 50-day average price"`` or
        ``"the average price over fast days"`` for a simple moving average
        with a fixed or a searched period.

    Raises:
        ValueError: If the indicator has no words here.
    """
    if ref.indicator == "close":
        return "the price"
    if ref.indicator == "sma":
        if ref.period is not None:
            return f"the {_number(ref.period)}-day average price"
        return f"the average price over {ref.period_param} days"
    raise ValueError(f"no words for indicator {ref.indicator!r}")


def condition_words(condition: Condition) -> str:
    """Word one condition as a sentence.

    Args:
        condition: A parsed condition.

    Returns:
        For example ``"The average price over fast days crosses above the
        average price over slow days."``

    Raises:
        ValueError: If the operator or either indicator has no words here.
    """
    if condition.operator not in OPERATOR_WORDS:
        raise ValueError(f"no words for operator {condition.operator!r}")
    left = _side(condition.left)
    right = (
        _side(condition.right)
        if isinstance(condition.right, IndicatorRef)
        else _number(condition.right)
    )
    sentence = f"{left} {OPERATOR_WORDS[condition.operator]} {right}."
    return sentence[0].upper() + sentence[1:]


def axis_size(spec: dict[str, float]) -> int:
    """Count the values one searched range takes, without listing them.

    The count :func:`~strategylab.search.grid.expand_grid` documents for one
    axis, ``floor((max - min) / step) + 1``, worked out rather than built: a
    range of millions of values is a config the loader accepts, and listing it
    to count it would stall the app's first screen. A test holds this to
    ``len(expand_grid(...))`` on ranges small enough to list.

    Args:
        spec: ``min``, ``max`` and ``step``, whole numbers of days with ``step``
            positive and ``max`` at least ``min``, as the loader guarantees.

    Returns:
        How many values the search walks along this axis.
    """
    return int((spec["max"] - spec["min"]) // spec["step"]) + 1


def _range_words(name: str, spec: dict[str, float]) -> str:
    """Word one searched range.

    Args:
        name: The parameter's name.
        spec: Its ``min``, ``max`` and ``step``, in days for a lookback.

    Returns:
        For example ``"fast: 5 to 30 days, in steps of 5 (6 values)."``
    """
    low, high, step = spec["min"], spec["max"], spec["step"]
    count = axis_size(spec)
    return (
        f"{name}: {_number(low)} to {_number(high)} days, in steps of {_number(step)} "
        f"({count} value{'s' if count != 1 else ''})."
    )


def _fixed_words(name: str, value: float) -> str:
    """Word one fixed parameter.

    Args:
        name: The parameter's name.
        value: Its value, in its own units.

    Returns:
        For the position size, ``"Each trade uses 95% of the account."``; for
        any other, ``"name is 50."``
    """
    if name == POSITION_SIZE:
        return f"Each trade uses {_number(value)}% of the account."
    return f"{name} is {_number(value)}."


def describe(definition: StrategyDef) -> dict[str, Any]:
    """Word a strategy's rules for the draft page.

    Args:
        definition: The parsed strategy.

    Returns:
        ``entry`` and ``exit``, one sentence per condition in the order the
        file gives them - every entry condition must hold to buy, every exit
        condition to sell; ``search``, one sentence per searched range;
        ``fixed``, one per fixed parameter; and ``versions``, how many
        combinations the search walks, the product of :func:`axis_size`
        over the ranges.

    Raises:
        ValueError: If an indicator or operator has no words here.
    """
    return {
        "entry": [condition_words(condition) for condition in definition.entry],
        "exit": [condition_words(condition) for condition in definition.exit],
        "search": [_range_words(name, spec) for name, spec in definition.search.items()],
        "fixed": [_fixed_words(name, value) for name, value in definition.fixed.items()],
        "versions": math.prod(axis_size(spec) for spec in definition.search.values()),
    }
