"""Strategy evaluation. Used by BOTH the backtester and the live worker.

Written once. Never duplicated elsewhere: two implementations of the same
strategy drift, and when they drift the backtest silently stops describing the
thing that is actually trading.

The vocabulary implemented here is the vocabulary
:mod:`strategylab.core.config` enforces at load time. The two must not drift
either: a name the loader accepts and this module cannot compute is a config
that passes validation and fails at the first bar.

WHAT THIS MODULE DOES NOT KNOW. It has no idea whether a position is currently
open. :func:`evaluate` reports what the strategy WANTS - enter, exit, or neither
- and the caller, which is the only party that knows the position, decides what
to do about it. That is what makes the same function usable by a backtester
replaying 2005 and by a live worker looking at this morning.
"""

from __future__ import annotations

import math

import pandas as pd

from strategylab.core.types import Action, Condition, IndicatorRef, Intent, StrategyDef
from strategylab.data.source import REQUIRED_COLUMNS

# Operators that read the bar before the current one as well as the current one.
# Their presence raises the minimum history by exactly one bar, and getting that
# wrong produces a NaN comparison rather than an error - which is why the
# requirement is computed rather than assumed.
_CROSSING_OPERATORS = frozenset({"crosses_above", "crosses_below"})

# The parameter naming the position size, read from the definition's ``fixed``
# block. Not defaulted: see _size_pct.
_SIZE_PARAM = "position_size_pct"


def _validate_frame(bars: pd.DataFrame) -> None:
    """Check the frame is one this module can evaluate against.

    Args:
        bars: The candidate window of OHLCV bars.

    Raises:
        ValueError: If ``bars`` is empty, lacks a required column, or is not
            sorted strictly ascending by its index.
    """
    if bars.empty:
        raise ValueError("bars is empty; there is no latest bar to evaluate")

    missing = [column for column in REQUIRED_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"bars lacks required column(s) {missing}; got {list(bars.columns)}")

    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars is not sorted ascending by its index")
    if bars.index.has_duplicates:
        raise ValueError("bars carries duplicate timestamps; the latest bar is ambiguous")


def _resolve_period(
    ref: IndicatorRef, definition: StrategyDef, params: dict[str, int]
) -> int | None:
    """Return the lookback for one indicator reference, in bars.

    A ``$placeholder`` is looked up in ``params`` first and in the definition's
    ``fixed`` block second, which is the same order
    :func:`strategylab.core.config.load_strategy` validates against: the loader
    guarantees every placeholder has a home in one of the two, and the searched
    ones arrive here through ``params``.

    Args:
        ref: The reference to resolve.
        definition: The strategy, for its ``fixed`` block.
        params: Resolved placeholder values, keyed without the leading ``$``,
            in bars.

    Returns:
        The lookback in bars, or ``None`` for an indicator that takes none.

    Raises:
        ValueError: If a referenced placeholder is in neither source, or if its
            value is not a whole number of bars of at least one.
    """
    if ref.period is not None:
        return ref.period
    if ref.period_param is None:
        return None

    name = ref.period_param
    if name in params:
        value: float = params[name]
    elif name in definition.fixed:
        value = definition.fixed[name]
    else:
        raise ValueError(
            f"params omits '{name}', which indicator '{ref.indicator}' references as ${name}"
        )

    if isinstance(value, bool) or value != int(value) or value < 1:
        raise ValueError(
            f"parameter '{name}' must be a whole number of bars of at least 1, got {value!r}"
        )
    return int(value)


def _series(ref: IndicatorRef, period: int | None, bars: pd.DataFrame) -> pd.Series:
    """Compute one indicator series over the window.

    Every indicator here is causal by construction: ``rolling`` looks backward
    only, and ``min_periods`` equal to the window means the first ``period - 1``
    values are NaN rather than a mean of however many bars happened to be
    available.

    Honest note on that ``min_periods``, because a reader will otherwise assume
    a test protects it. It does not, and cannot: :func:`evaluate` refuses a frame
    holding fewer rows than the longest lookback needs, so the warm-up region is
    never read, so ``min_periods=1`` would produce identical output on every
    input this function is allowed to receive. It is written this way because it
    is the correct expression of a fixed-width window, not because anything
    downstream would notice if it were not. What IS tested is the property that
    matters: the value at a bar does not change with the amount of history in
    front of it, which is what fails the moment ``rolling`` becomes
    ``expanding``.

    Args:
        ref: The indicator reference.
        period: Its resolved lookback in bars, or ``None`` if it takes none.
        bars: The window of OHLCV bars.

    Returns:
        The indicator's value at every bar in the window.

    Raises:
        ValueError: If the indicator name is not one this module computes.
    """
    if ref.indicator == "close":
        return bars["close"]
    if ref.indicator == "sma":
        # period is not None here: config.INDICATORS says sma takes one, and
        # _resolve_period returns it or raises.
        assert period is not None
        return bars["close"].rolling(window=period, min_periods=period).mean()
    raise ValueError(
        f"indicator '{ref.indicator}' is in the config vocabulary but is not implemented here; "
        "the loader and the engine have drifted"
    )


def _value_at(side: pd.Series | float, offset: int) -> float:
    """Read one side of a comparison at a position counted back from the end.

    Args:
        side: An indicator series, or a literal from the config.
        offset: ``0`` for the latest bar, ``1`` for the bar before it.

    Returns:
        The value at that bar, or the literal, which is the same at every bar.
    """
    if isinstance(side, pd.Series):
        return float(side.iloc[-1 - offset])
    return float(side)


def _lookback_of(ref: IndicatorRef, definition: StrategyDef, params: dict[str, int]) -> int:
    """Return how many bars of history one reference needs, at minimum.

    Args:
        ref: The indicator reference.
        definition: The strategy, for its ``fixed`` block.
        params: Resolved placeholder values, in bars.

    Returns:
        The lookback in bars; ``1`` for an indicator that takes no period, since
        it still needs the bar it is read on.
    """
    period = _resolve_period(ref, definition, params)
    return 1 if period is None else period


def _required_history(definition: StrategyDef, params: dict[str, int]) -> int:
    """Return the minimum number of bars needed to evaluate every condition.

    The longest lookback in the strategy, plus one more bar if any operator
    compares the current bar against the previous one.

    Args:
        definition: The parsed strategy.
        params: Resolved placeholder values, in bars.

    Returns:
        The minimum row count, in bars.
    """
    longest = 1
    crossing = False
    for condition in (*definition.entry, *definition.exit):
        longest = max(longest, _lookback_of(condition.left, definition, params))
        if isinstance(condition.right, IndicatorRef):
            longest = max(longest, _lookback_of(condition.right, definition, params))
        if condition.operator in _CROSSING_OPERATORS:
            crossing = True
    return longest + (1 if crossing else 0)


def _render(condition: Condition, left: float, right: float) -> str:
    """Render one evaluated condition for a decision log.

    The VALUES are included, not just the names. A log line saying
    ``sma(20) crosses_above sma(50)`` records what the rule was; one saying
    ``sma(20)=101.5 crosses_above sma(50)=100.2`` records why it fired, which is
    the thing anybody reading the log afterwards actually wants.

    Args:
        condition: The condition that was evaluated.
        left: The left side's value at the latest bar.
        right: The right side's value at the latest bar.

    Returns:
        A single line naming both sides, their values, and the operator.
    """

    def name(ref: IndicatorRef | float) -> str:
        if not isinstance(ref, IndicatorRef):
            return f"{ref:.6g}"
        if ref.period is not None:
            return f"{ref.indicator}({ref.period})"
        if ref.period_param is not None:
            return f"{ref.indicator}(${ref.period_param})"
        return ref.indicator

    return (
        f"{name(condition.left)}={left:.6g} "
        f"{condition.operator} "
        f"{name(condition.right)}={right:.6g}"
    )


def _holds(
    condition: Condition, bars: pd.DataFrame, resolved: dict[int, pd.Series | float]
) -> bool:
    """Evaluate one condition at the latest bar.

    Args:
        condition: The condition to evaluate.
        bars: The window of OHLCV bars.
        resolved: Precomputed series for this condition, keyed by ``id()`` of
            its left and right references.

    Returns:
        Whether the condition holds at the latest bar.

    Raises:
        ValueError: If the operator is not one this module implements, or if a
            value needed for the comparison is not finite. A NaN would compare
            False against everything and turn a data problem into a strategy
            that silently never trades.
    """
    left = resolved[id(condition.left)]
    right = (
        resolved[id(condition.right)]
        if isinstance(condition.right, IndicatorRef)
        else (float(condition.right))
    )

    now_left, now_right = _value_at(left, 0), _value_at(right, 0)
    for label, value in (("left", now_left), ("right", now_right)):
        if not math.isfinite(value):
            raise ValueError(
                f"the {label} side of '{condition.operator}' is {value} at the latest bar; "
                "a non-finite value compares False against everything, which would look "
                "like a strategy that simply never trades"
            )

    if condition.operator == "above":
        return now_left > now_right

    if condition.operator in _CROSSING_OPERATORS:
        prev_left, prev_right = _value_at(left, 1), _value_at(right, 1)
        for label, value in (("left", prev_left), ("right", prev_right)):
            if not math.isfinite(value):
                raise ValueError(
                    f"the {label} side of '{condition.operator}' is {value} at the bar before "
                    "the latest one, so the crossing cannot be decided"
                )
        if condition.operator == "crosses_above":
            return prev_left <= prev_right and now_left > now_right
        return prev_left >= prev_right and now_left < now_right

    raise ValueError(
        f"operator '{condition.operator}' is in the config vocabulary but is not implemented "
        "here; the loader and the engine have drifted"
    )


def _test_all(
    conditions: tuple[Condition, ...], bars: pd.DataFrame, resolved: dict[int, pd.Series | float]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Evaluate a whole AND-combined condition list.

    Every condition is evaluated, including the ones after the first failure.
    Short-circuiting would be faster and would leave the decision log unable to
    say which of three rules blocked an entry.

    Args:
        conditions: The list to evaluate, combined with AND.
        bars: The window of OHLCV bars.
        resolved: Precomputed series, keyed by ``id()`` of each reference.

    Returns:
        Rendered lines for the conditions that held and for those that did not,
        each in the order the config gives them.
    """
    met: list[str] = []
    failed: list[str] = []
    for condition in conditions:
        left = resolved[id(condition.left)]
        right = (
            resolved[id(condition.right)]
            if isinstance(condition.right, IndicatorRef)
            else float(condition.right)
        )
        line = _render(condition, _value_at(left, 0), _value_at(right, 0))
        (met if _holds(condition, bars, resolved) else failed).append(line)
    return tuple(met), tuple(failed)


def _size_pct(definition: StrategyDef) -> float:
    """Return the requested position size, as a percentage of account equity.

    Not defaulted. A strategy that does not say how much to buy has not been
    fully specified, and silently choosing a number here - 100% being the
    tempting one - would size a real position from an omission.

    Args:
        definition: The parsed strategy.

    Returns:
        The size in percent, so ``95.0`` means ninety-five percent.

    Raises:
        ValueError: If ``fixed.position_size_pct`` is absent, or is not a
            percentage between zero and one hundred.
    """
    if _SIZE_PARAM not in definition.fixed:
        raise ValueError(
            f"the strategy can enter a position but its 'fixed' block omits '{_SIZE_PARAM}', "
            "so it never says how much to buy"
        )
    size = definition.fixed[_SIZE_PARAM]
    if not 0.0 < size <= 100.0:
        raise ValueError(
            f"'{_SIZE_PARAM}' is a percentage of equity and must be above 0 and at most 100, "
            f"got {size}"
        )
    return size


def evaluate(definition: StrategyDef, bars: pd.DataFrame, params: dict[str, int]) -> Intent:
    """Return the strategy's intent for the LATEST bar in ``bars``.

    Pure. Given a strategy definition, a window of bars, and resolved
    parameters, this returns the intent for the latest bar and nothing else. It
    has no knowledge of wall-clock time, of execution, or of where the bars came
    from, which is what allows the backtester and the live worker to share it.

    LOOKAHEAD CONSTRAINT: reads ``bars`` up to and including the last row and
    nothing after it. Note where that guarantee actually comes from: the last
    row IS the bar being evaluated, so the caller decides what this function can
    see by choosing what to pass. The tests hold up both halves - that this
    function does not reach past what it is given, and that its answer for a bar
    does not change with the amount of history in front of it, which is the
    leak a rolling window silently turns into an expanding one.

    WHETHER A POSITION IS OPEN IS NOT KNOWN HERE. ``ENTER_LONG`` means every
    entry condition holds, not that anything should be bought; ``EXIT_LONG``
    means every exit condition holds. The caller holds the position and decides.
    When both lists hold on the same bar, ``EXIT_LONG`` wins: flat is the
    recoverable state, and a strategy whose entry and exit fire together is one
    whose config should be read again.

    Args:
        definition: The parsed strategy, whose ``entry`` and ``exit`` condition
            lists are each combined with AND.
        bars: OHLCV bars indexed by timestamp in ascending order. The last row
            is the bar being evaluated. Must hold enough history to satisfy the
            longest lookback in ``params``, plus one more bar if any condition
            uses a crossing operator.
        params: Resolved values for the strategy's ``$placeholder`` parameters,
            keyed without the leading ``$``, in bars.

    Returns:
        The intent for the latest bar, carrying which conditions passed and
        which did not. For a ``HOLD`` those are the ENTRY conditions, because
        the question a reader of the log is asking is why nothing was bought.

    Raises:
        ValueError: If ``bars`` is empty, is not sorted ascending, carries
            duplicate timestamps, lacks a required column, or holds fewer rows
            than the longest lookback needs; if ``params`` omits a placeholder
            the definition references; if a condition names an indicator or
            operator this module does not implement; if a value needed for a
            comparison is not finite; or if the strategy can enter but does not
            declare ``fixed.position_size_pct``.
    """
    _validate_frame(bars)

    needed = _required_history(definition, params)
    if len(bars) < needed:
        raise ValueError(
            f"bars holds {len(bars)} rows but this strategy needs {needed} to evaluate its "
            "longest lookback; evaluating on fewer would compare against a NaN and report a "
            "HOLD that means 'not enough data' while looking like 'no signal'"
        )

    # One series per REFERENCE, not per condition: two conditions naming the
    # same sma share the computation, and keying by id() keeps two references
    # that happen to be equal but distinct from colliding.
    resolved: dict[int, pd.Series | float] = {}
    for condition in (*definition.entry, *definition.exit):
        for side in (condition.left, condition.right):
            if isinstance(side, IndicatorRef) and id(side) not in resolved:
                period = _resolve_period(side, definition, params)
                resolved[id(side)] = _series(side, period, bars)

    exit_met, exit_failed = _test_all(definition.exit, bars, resolved)
    if not exit_failed:
        return Intent(action=Action.EXIT_LONG, size_pct=0.0, conditions_met=exit_met)

    entry_met, entry_failed = _test_all(definition.entry, bars, resolved)
    if not entry_failed:
        return Intent(
            action=Action.ENTER_LONG, size_pct=_size_pct(definition), conditions_met=entry_met
        )

    return Intent(
        action=Action.HOLD,
        size_pct=0.0,
        conditions_met=entry_met,
        conditions_failed=entry_failed,
    )
