"""Tests for strategy evaluation, including the lookahead test.

`CLAUDE.md` calls the lookahead test the single most important one in this
project. It is here, and so is an honest statement of what it does and does not
prove, because a test trusted for more than it establishes is worse than none.

`evaluate` receives a window whose LAST ROW is the bar being evaluated. It
therefore cannot read the future in the ordinary sense: there is no future in
the frame it is handed. The test the project prompt specifies - corrupt every
bar after `t`, assert the intent at `t` is unchanged - is therefore not merely
weak. It CANNOT FAIL: the corrupted rows are sliced off before the call, so both
invocations receive identical data. An earlier version of this docstring called
it "close to true by construction", which was too generous by exactly the margin
that matters. It is named
`test_the_specified_corruption_test_which_no_implementation_can_fail` and it
asserts its own vacuity, so that nobody reads the suite and concludes lookahead
is covered.

The leak that IS possible with this signature is subtler, and
`test_the_intent_at_t_does_not_depend_on_how_much_history_precedes_it` is the
one that catches it: an indicator computed with `.expanding()` where `.rolling()`
was meant, or a `min_periods` shorter than the window, makes the value at bar t
depend on how far back the frame happens to start. That bug does not read the
future, it reads too much of the past, and it produces exactly the same symptom -
a backtest that cannot be reproduced live, where the number was better than the
strategy.

Expected values here are hand-computed from the close series and are recorded in
the comments beside them.
"""

from __future__ import annotations

import pandas as pd
import pytest

from strategylab.core.strategy import evaluate
from strategylab.core.types import Action, Condition, IndicatorRef, Intent, StrategyDef

# The close series every unit test below is built on. Chosen so that a 2-bar and
# a 3-bar SMA cross exactly once, at a bar far enough in to be evaluable:
#
#   t      0     1      2      3      4        5      6      7
#   close  13    12     11     10     11       12     13     14
#   sma2   -     12.5   11.5   10.5   10.5     11.5   12.5   13.5
#   sma3   -     -      12.0   11.0   10.6667  11.0   12.0   13.0
#   fast?        -      <      <      <        >      >      >
#
# So sma2 crosses ABOVE sma3 at t=5: at t=4 it is 10.5 <= 10.6667, and at t=5 it
# is 11.5 > 11.0. That is the only crossing in the series.
V_SHAPE = [13.0, 12.0, 11.0, 10.0, 11.0, 12.0, 13.0, 14.0]
CROSS_BAR = 5


def frame(closes: list[float]) -> pd.DataFrame:
    """Build an OHLCV frame from a close series.

    The other three price columns are NOT constant offsets of the close, and the
    reason is worth stating. They were, and that made this helper's own docstring
    false: a crossover compares two moving averages of the same column, so a
    constant offset cancels exactly in the difference and `open` produced a
    signal identical to `close` at every bar. No test here could tell which
    column the implementation read. The offsets now vary bar to bar, so a wrong
    column changes the answer, and
    `test_only_the_close_column_moves_the_signal` pins it directly.

    Args:
        closes: Close price per bar, in order.

    Returns:
        A frame indexed by consecutive business days.
    """
    index = pd.bdate_range("2020-01-01", periods=len(closes), name="date")
    # A varying, non-cancelling perturbation. The pattern repeats every three
    # bars so it stays hand-checkable.
    wobble = [0.3, -0.7, 1.1]
    opens = [c + wobble[i % 3] for i, c in enumerate(closes)]
    highs = [max(opens[i], c) + 1.0 + wobble[i % 3] for i, c in enumerate(closes)]
    lows = [min(opens[i], c) - 1.0 - wobble[i % 3] for i, c in enumerate(closes)]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000 + i for i in range(len(closes))],
        },
        index=index,
    )


def crossover(fast: str = "$fast", slow: str = "$slow") -> StrategyDef:
    """Build a crossover strategy shaped like the committed config.

    Args:
        fast: Placeholder or literal for the fast lookback.
        slow: Placeholder or literal for the slow lookback.

    Returns:
        A definition whose entry is a fast-over-slow cross filtered to an
        uptrend, and whose exit is the reverse cross.
    """

    def ref(spec: str) -> IndicatorRef:
        if spec.startswith("$"):
            return IndicatorRef(indicator="sma", period_param=spec[1:])
        return IndicatorRef(indicator="sma", period=int(spec))

    return StrategyDef(
        name="crossover",
        version=1,
        description="Test fixture.",
        entry=(
            Condition(left=ref(fast), operator="crosses_above", right=ref(slow)),
            Condition(left=IndicatorRef(indicator="close"), operator="above", right=ref(slow)),
        ),
        exit=(Condition(left=ref(fast), operator="crosses_below", right=ref(slow)),),
        search={},
        fixed={"position_size_pct": 95.0},
    )


PARAMS = {"fast": 2, "slow": 3}
# Longest lookback is 3, and both crossing operators need the bar before the
# current one, so four rows is the minimum this strategy can be evaluated on.
NEEDED = 4


# --------------------------------------------------------------------------
# THE LOOKAHEAD TEST. Read the module docstring for what each half proves.
# --------------------------------------------------------------------------


def test_the_specified_corruption_test_which_no_implementation_can_fail() -> None:
    # THIS TEST CANNOT FAIL, and it is named so nobody has to discover that.
    #
    # It is the test .claude/setup.md specifies: corrupt every bar after t to
    # absurd values and assert the intent at t is unchanged. But `evaluate`
    # takes a window whose last row IS bar t, so the corrupted rows are sliced
    # away before the call and both invocations receive value-identical frames.
    # An auditor confirmed the inputs are bit-identical. No implementation, no
    # matter how badly it peeked, could fail this.
    #
    # It is kept for one narrow reason: it pins the SIGNATURE. If `evaluate`
    # ever grows a full-frame-plus-index form, the slicing below stops removing
    # the corruption and this test starts having teeth - and until then, its
    # presence and this comment stop anyone reading the suite and concluding
    # that lookahead is covered. The test that covers it is the next one.
    bars = frame(V_SHAPE)
    definition = crossover()
    for t in range(NEEDED - 1, len(bars)):
        clean = evaluate(definition, bars.iloc[: t + 1], PARAMS)
        corrupted = bars.copy()
        corrupted.iloc[t + 1 :] = 1e12
        # Proof of the vacuity, asserted rather than claimed: what the two calls
        # receive is the same data.
        pd.testing.assert_frame_equal(bars.iloc[: t + 1], corrupted.iloc[: t + 1], check_exact=True)
        assert evaluate(definition, corrupted.iloc[: t + 1], PARAMS) == clean


def test_the_intent_at_t_does_not_depend_on_how_much_history_precedes_it() -> None:
    # The half with teeth. A 3-bar SMA at bar t is a function of exactly three
    # closes; if the value moves when the frame starts earlier, the window is
    # expanding rather than rolling and every backtested number is computed
    # from more data than the live worker will ever have at that bar.
    long_history = frame([20.0] * 40 + V_SHAPE)
    t = len(long_history) - 1
    definition = crossover()
    reference = evaluate(definition, long_history, PARAMS)
    for start in range(0, t + 1 - NEEDED + 1):
        window = long_history.iloc[start : t + 1]
        assert evaluate(definition, window, PARAMS) == reference, (
            f"the intent moved when the frame started at row {start} instead of 0"
        )


def test_evaluate_does_not_modify_the_frame_it_is_given() -> None:
    # A function that rewrites its caller's data cannot be reasoned about bar by
    # bar, and the corruption test above would not notice.
    #
    # check_exact is not optional here. assert_frame_equal defaults to rtol=1e-5,
    # and this test previously passed against an implementation that multiplied
    # every close by 1.0000001 - a mutation that is invisible at that tolerance
    # and would compound into a different equity curve over 5,000 bars.
    bars = frame(V_SHAPE)
    before = bars.copy(deep=True)
    evaluate(crossover(), bars, PARAMS)
    pd.testing.assert_frame_equal(bars, before, check_exact=True)


def test_only_the_close_column_moves_the_signal() -> None:
    # The vocabulary is defined on the close. Until the wobble was added to
    # `frame`, open/high/low were constant offsets of close - and a constant
    # offset cancels exactly in a difference of two moving averages, so an
    # implementation reading `open` produced an identical signal and no test
    # here could have told.
    bars = frame(V_SHAPE).iloc[: CROSS_BAR + 1]
    definition = crossover()
    reference = evaluate(definition, bars, PARAMS)

    for column in ("open", "high", "low", "volume"):
        moved = bars.copy()
        moved[column] = moved[column] * 3.0 + 17.0
        assert evaluate(definition, moved, PARAMS) == reference, (
            f"the intent changed when only '{column}' moved, so something reads it"
        )

    moved_close = bars.copy()
    moved_close["close"] = moved_close["close"] * 3.0 + 17.0
    assert evaluate(definition, moved_close, PARAMS) != reference, (
        "the intent did not change when the close moved, so the test above proves nothing"
    )


def test_evaluate_is_deterministic() -> None:
    bars = frame(V_SHAPE)
    definition = crossover()
    assert evaluate(definition, bars, PARAMS) == evaluate(definition, bars, PARAMS)


# --------------------------------------------------------------------------
# The signal itself, against hand-computed values.
# --------------------------------------------------------------------------


def test_the_entry_fires_on_the_bar_where_the_averages_cross() -> None:
    # At t=5: sma2 = (11+12)/2 = 11.5, sma3 = (10+11+12)/3 = 11.0, so the fast
    # average is above the slow one; at t=4 it was 10.5 against 10.6667, so it
    # was not. That is a cross. close = 12 > sma3 = 11.0, so the trend filter
    # holds too, and both entry conditions are met.
    bars = frame(V_SHAPE).iloc[: CROSS_BAR + 1]
    intent = evaluate(crossover(), bars, PARAMS)
    assert intent.action is Action.ENTER_LONG
    assert intent.size_pct == 95.0
    assert intent.conditions_failed == ()
    assert len(intent.conditions_met) == 2
    # Equality, not containment. `"sma($slow)=11" in "...sma($slow)=11.5"` is
    # True, so the containment form passed against the very value it exists to
    # distinguish 11.0 from.
    assert intent.conditions_met[0] == "sma($fast)=11.5 crosses_above sma($slow)=11"
    assert intent.conditions_met[1] == "close=12 above sma($slow)=11"


@pytest.mark.parametrize("t", [3, 4, 6, 7])
def test_no_entry_on_any_bar_that_is_not_the_crossing(t: int) -> None:
    # t=3 and t=4 are below the slow average, t=6 and t=7 are above it but the
    # cross already happened at t=5. Only a CROSSING enters.
    intent = evaluate(crossover(), frame(V_SHAPE).iloc[: t + 1], PARAMS)
    assert intent.action is not Action.ENTER_LONG


def test_a_hold_reports_the_entry_conditions_that_failed() -> None:
    # The question a reader of the log is asking is why nothing was bought.
    intent = evaluate(crossover(), frame(V_SHAPE).iloc[:7], PARAMS)
    assert intent.action is Action.HOLD
    assert intent.size_pct == 0.0
    # At t=6 the cross has passed but the trend filter still holds, so exactly
    # one of the two entry conditions failed and the other did not.
    assert len(intent.conditions_failed) == 1
    assert " crosses_above " in intent.conditions_failed[0]
    assert len(intent.conditions_met) == 1
    # " above " with spaces, because "above" is a substring of "crosses_above"
    # and the bare form is satisfied by the operator it is distinguishing from.
    assert " above " in intent.conditions_met[0]
    assert "crosses" not in intent.conditions_met[0]


def test_the_exit_fires_on_the_reverse_cross() -> None:
    # Mirror of the entry series: sma2 crosses BELOW sma3 at t=5.
    bars = frame([10.0, 11.0, 12.0, 13.0, 12.0, 11.0])
    intent = evaluate(crossover(), bars, PARAMS)
    assert intent.action is Action.EXIT_LONG
    assert intent.size_pct == 0.0


def test_every_condition_is_evaluated_even_after_one_fails() -> None:
    # Short-circuiting an AND would be faster and would leave the log unable to
    # say which of several rules blocked an entry.
    definition = StrategyDef(
        name="three",
        version=1,
        description="Test fixture.",
        entry=(
            Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),
            Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),
            Condition(left=IndicatorRef(indicator="close"), operator="above", right=0.0),
        ),
        exit=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),),
        search={},
        fixed={"position_size_pct": 95.0},
    )
    intent = evaluate(definition, frame(V_SHAPE), {})
    assert len(intent.conditions_failed) == 2
    assert len(intent.conditions_met) == 1


def test_exit_wins_when_entry_and_exit_both_hold() -> None:
    # Flat is the recoverable state. A config that does this should be reread,
    # but the engine must not have to guess.
    always = Condition(left=IndicatorRef(indicator="close"), operator="above", right=0.0)
    definition = StrategyDef(
        name="both",
        version=1,
        description="Test fixture.",
        entry=(always,),
        exit=(always,),
        search={},
        fixed={"position_size_pct": 95.0},
    )
    assert evaluate(definition, frame(V_SHAPE), {}).action is Action.EXIT_LONG


def test_above_is_strict_so_equality_is_not_above() -> None:
    # `above` must be `>`, not `>=`. Without this the boundary is untested, and
    # on a flat series - which a real one is, at every level it revisits - a
    # `>=` fires on bars where nothing moved.
    definition = StrategyDef(
        name="boundary",
        version=1,
        description="Test fixture.",
        entry=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=14.0),),
        exit=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),),
        search={},
        fixed={"position_size_pct": 50.0},
    )
    # The final close is exactly 14.0. Equal is not above.
    assert evaluate(definition, frame(V_SHAPE), {}).action is Action.HOLD


def test_crossing_at_exact_equality_counts_as_below_on_the_prior_bar() -> None:
    # `crosses_above` reads `prev_left <= prev_right`, so touching from below and
    # then breaking out IS a cross. Pinned because the alternative - a strict
    # `<` - silently drops every crossing that pauses on the line first, and
    # neither version fails loudly.
    #   t      0     1     2     3
    #   close  10    10    10    16
    #   sma2   -     10    10    13
    #   sma3   -     -     10    12
    # At t=2 both averages are exactly 10.0; at t=3, 13 > 12. That is a cross.
    bars = frame([10.0, 10.0, 10.0, 16.0])
    definition = crossover()
    intent = evaluate(definition, bars, PARAMS)
    assert intent.action is Action.ENTER_LONG


def test_a_literal_right_hand_side_is_compared_as_a_constant() -> None:
    definition = StrategyDef(
        name="literal",
        version=1,
        description="Test fixture.",
        entry=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=13.5),),
        exit=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),),
        search={},
        fixed={"position_size_pct": 50.0},
    )
    # Final close is 14.0, which is above 13.5.
    assert evaluate(definition, frame(V_SHAPE), {}).action is Action.ENTER_LONG
    # ... and 14.0 is not above 14.5.
    definition_high = StrategyDef(
        name="literal",
        version=1,
        description="Test fixture.",
        entry=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=14.5),),
        exit=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),),
        search={},
        fixed={"position_size_pct": 50.0},
    )
    assert evaluate(definition_high, frame(V_SHAPE), {}).action is Action.HOLD


def test_a_fixed_parameter_can_supply_a_period() -> None:
    definition = crossover()
    definition = StrategyDef(
        name=definition.name,
        version=1,
        description=definition.description,
        entry=definition.entry,
        exit=definition.exit,
        search={},
        fixed={"position_size_pct": 95.0, "slow": 3.0},
    )
    intent = evaluate(definition, frame(V_SHAPE).iloc[: CROSS_BAR + 1], {"fast": 2})
    assert intent.action is Action.ENTER_LONG


# --------------------------------------------------------------------------
# Refusals. Each is a bug that would otherwise look like "no signal".
# --------------------------------------------------------------------------


def test_too_little_history_raises_rather_than_reporting_a_hold() -> None:
    # This is the important one of the refusals. An SMA with too few bars is
    # NaN, NaN compares False against everything, and every condition fails -
    # so a strategy starved of history looks exactly like a strategy with no
    # signal, forever, and nothing anywhere says otherwise.
    bars = frame(V_SHAPE).iloc[: NEEDED - 1]
    with pytest.raises(ValueError, match=f"holds {NEEDED - 1} rows but this strategy needs 4"):
        evaluate(crossover(), bars, PARAMS)


def test_exactly_enough_history_is_accepted() -> None:
    # The other side of the boundary, so the check is proven to be `<` and not
    # `<=`.
    bars = frame(V_SHAPE).iloc[:NEEDED]
    assert isinstance(evaluate(crossover(), bars, PARAMS), Intent)


def test_an_empty_frame_is_refused() -> None:
    with pytest.raises(ValueError, match="bars is empty"):
        evaluate(crossover(), frame([]), PARAMS)


def test_a_frame_out_of_order_is_refused() -> None:
    bars = frame(V_SHAPE).iloc[::-1]
    with pytest.raises(ValueError, match="not sorted ascending"):
        evaluate(crossover(), bars, PARAMS)


def test_duplicate_timestamps_are_refused() -> None:
    bars = frame(V_SHAPE)
    bars.index = pd.DatetimeIndex([bars.index[0]] * len(bars), name="date")
    with pytest.raises(ValueError, match="duplicate timestamps"):
        evaluate(crossover(), bars, PARAMS)


@pytest.mark.parametrize("column", ["open", "high", "low", "close", "volume"])
def test_a_missing_column_is_refused(column: str) -> None:
    bars = frame(V_SHAPE).drop(columns=[column])
    with pytest.raises(ValueError, match=f"lacks required column.*{column}"):
        evaluate(crossover(), bars, PARAMS)


def test_a_placeholder_with_no_value_is_refused() -> None:
    with pytest.raises(ValueError, match="params omits 'slow'"):
        evaluate(crossover(), frame(V_SHAPE), {"fast": 2})


@pytest.mark.parametrize("value", [0, -3, 2.5])
def test_a_parameter_that_is_not_a_whole_positive_bar_count_is_refused(value: float) -> None:
    with pytest.raises(ValueError, match="parameter 'slow' must be a whole number of bars"):
        evaluate(crossover(), frame(V_SHAPE), {"fast": 2, "slow": value})  # type: ignore[dict-item]


def test_an_indicator_the_engine_cannot_compute_is_refused() -> None:
    # The loader's vocabulary and this module's must not drift. If one grows a
    # name the other does not have, the failure has to be loud and has to say
    # which two things disagree.
    definition = StrategyDef(
        name="drifted",
        version=1,
        description="Test fixture.",
        # Period 3, not 14: the history check runs first, and a lookback longer
        # than the fixture would refuse for that reason instead of this one.
        entry=(
            Condition(left=IndicatorRef(indicator="rsi", period=3), operator="above", right=30.0),
        ),
        exit=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),),
        search={},
        fixed={"position_size_pct": 95.0},
    )
    with pytest.raises(ValueError, match="loader and the engine have drifted"):
        evaluate(definition, frame(V_SHAPE), {})


def test_an_operator_the_engine_cannot_apply_is_refused() -> None:
    definition = StrategyDef(
        name="drifted",
        version=1,
        description="Test fixture.",
        entry=(Condition(left=IndicatorRef(indicator="close"), operator="below", right=1e9),),
        exit=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),),
        search={},
        fixed={"position_size_pct": 95.0},
    )
    with pytest.raises(ValueError, match="loader and the engine have drifted"):
        evaluate(definition, frame(V_SHAPE), {})


def test_a_strategy_that_can_enter_must_say_how_much_to_buy() -> None:
    # Silently defaulting to 100% would size a real position from an omission.
    definition = StrategyDef(
        name="sizeless",
        version=1,
        description="Test fixture.",
        entry=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=0.0),),
        exit=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),),
        search={},
        fixed={},
    )
    with pytest.raises(ValueError, match="omits 'position_size_pct'"):
        evaluate(definition, frame(V_SHAPE), {})


@pytest.mark.parametrize("size", [0.0, -5.0, 100.5])
def test_a_position_size_outside_nought_to_a_hundred_percent_is_refused(size: float) -> None:
    definition = StrategyDef(
        name="sized",
        version=1,
        description="Test fixture.",
        entry=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=0.0),),
        exit=(Condition(left=IndicatorRef(indicator="close"), operator="above", right=1e9),),
        search={},
        fixed={"position_size_pct": size},
    )
    with pytest.raises(ValueError, match="must be above 0 and at most 100"):
        evaluate(definition, frame(V_SHAPE), {})


@pytest.mark.parametrize("empty", ["entry", "exit"])
def test_an_empty_condition_list_is_refused(empty: str) -> None:
    # An empty AND-list is vacuously TRUE, and the vacuous answer here is a
    # position: `exit=()` returned EXIT_LONG on every bar and `entry=()`
    # returned ENTER_LONG at the full declared size, both silently. The loader
    # refuses an empty list, but the loader is not on this function's path.
    always = Condition(left=IndicatorRef(indicator="close"), operator="above", right=0.0)
    definition = StrategyDef(
        name="vacuous",
        version=1,
        description="Test fixture.",
        entry=() if empty == "entry" else (always,),
        exit=() if empty == "exit" else (always,),
        search={},
        fixed={"position_size_pct": 95.0},
    )
    with pytest.raises(ValueError, match=f"'{empty}' list is empty"):
        evaluate(definition, frame(V_SHAPE), {})


def test_a_non_finite_close_is_refused_rather_than_compared() -> None:
    # NaN > anything is False, so without this the bar reports a HOLD and the
    # data problem never surfaces.
    bars = frame(V_SHAPE)
    bars.loc[bars.index[-1], "close"] = float("nan")
    with pytest.raises(ValueError, match="compares False against everything"):
        evaluate(crossover(), bars, PARAMS)
