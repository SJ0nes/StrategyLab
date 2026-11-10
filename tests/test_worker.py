"""Tests for the live worker's pure logic and the replay it is diffed against.

Nothing here has a venue, a clock or a socket. The bars are synthetic, the
balance and positions are literals, and the worker's functions are called with
them directly - which is the point of keeping the worker pure.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from strategylab.core.types import Action, Condition, IndicatorRef, Intent, Side, StrategyDef
from strategylab.execution.venue import Balance, Position
from strategylab.live.worker import (
    COMPARED_FIELDS,
    REPLAY_REASON,
    Session,
    compare,
    decide,
    load_session,
    order_for,
    portfolio_state,
    record,
    replay,
    save_session,
    trip_if_reached,
)
from strategylab.logging.decisions import DecisionRecord, read_decisions, write_decisions
from strategylab.risk.limits import RiskLimits, RiskVerdict, Verdict, check

PARAMS = {"fast": 3, "slow": 5}
TICKER = "VUAG.L"
NOW = datetime(2026, 9, 12, 16, 35, tzinfo=UTC)


def _definition() -> StrategyDef:
    fast = IndicatorRef(indicator="sma", period_param="fast")
    slow = IndicatorRef(indicator="sma", period_param="slow")
    return StrategyDef(
        name="crossover",
        version=1,
        description="Test fixture.",
        entry=(Condition(left=fast, operator="crosses_above", right=slow),),
        exit=(Condition(left=fast, operator="crosses_below", right=slow),),
        search={"fast": {"min": 3, "max": 3, "step": 1}, "slow": {"min": 5, "max": 5, "step": 1}},
        fixed={"position_size_pct": 95.0},
    )


def _bars(closes: list[float]) -> pd.DataFrame:
    index = pd.bdate_range("2026-08-03", periods=len(closes), name="date")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
        },
        index=index,
    )


#: Falls, then rises. With fast=3 and slow=5 the fast average crosses above the
#: slow one on the NINTH bar (index 8, close 99.0): sma3 = 96.33 against sma5 =
#: 96.0, having been 95.0 against 95.6 the bar before. Every later bar is a HOLD.
RISING_LATE = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 96.0, 99.0, 103.0, 108.0, 114.0]
CROSS_BAR = 8
#: A crossover needs its longest lookback plus one bar, so six bars for slow=5.
FIRST_EVALUABLE = 6


def _limits(**over: float | bool) -> RiskLimits:
    base: dict[str, float | bool] = {
        "max_position_pct": 95.0,
        "max_daily_loss_pct": 3.0,
        "max_open_exposure_pct": 95.0,
        "kill_switch": False,
    }
    base.update(over)
    return RiskLimits(**base)  # type: ignore[arg-type]


def _balance(equity: float = 10_000.0, cash: float = 10_000.0) -> Balance:
    return Balance(currency="GBP", cash=cash, equity=equity)


def _session(sod: float = 10_000.0) -> Session:
    return Session(day=date(2026, 9, 12), start_of_day_equity=sod)


def _allow(size: float) -> RiskVerdict:
    return RiskVerdict(verdict=Verdict.ALLOW, reason="no limit bound", limited_size_pct=size)


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


def test_a_new_session_opens_at_current_equity(tmp_path: Path) -> None:
    session = load_session(tmp_path / "s.json", date(2026, 9, 12), 12_345.0)
    assert session == Session(day=date(2026, 9, 12), start_of_day_equity=12_345.0)


def test_a_session_round_trips_and_is_kept_on_the_same_day(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save_session(Session(day=date(2026, 9, 12), start_of_day_equity=9_000.0), path)
    session = load_session(path, date(2026, 9, 12), 8_000.0)
    assert session.start_of_day_equity == 9_000.0


def test_a_stale_session_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save_session(Session(day=date(2026, 9, 11), start_of_day_equity=9_000.0), path)
    assert load_session(path, date(2026, 9, 12), 8_000.0).start_of_day_equity == 8_000.0


def test_a_corrupt_session_file_starts_a_new_session(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{oops", encoding="utf-8")
    assert load_session(path, date(2026, 9, 12), 8_000.0).start_of_day_equity == 8_000.0


def test_save_session_creates_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "s.json"
    save_session(_session(), path)
    assert json.loads(path.read_text())["sod"] == 10_000.0


# ---------------------------------------------------------------------------
# portfolio state
# ---------------------------------------------------------------------------


def test_portfolio_state_sums_positive_exposure_and_reports_no_realised_pnl() -> None:
    positions = [
        Position(ticker="A", quantity=1.0, average_price=1.0, market_value=3_000.0),
        Position(ticker="B", quantity=1.0, average_price=1.0, market_value=-500.0),
    ]
    state = portfolio_state(_balance(equity=9_500.0), positions, _session(10_000.0))
    # Per the docstring, exposure is summed over POSITIVE market values only, so
    # the -500 does not net against the 3,000.
    assert state.open_exposure == 3_000.0
    assert state.equity == 9_500.0
    assert state.start_of_day_equity == 10_000.0
    assert state.realised_pnl_today == 0.0
    assert state.as_of == date(2026, 9, 12)


# ---------------------------------------------------------------------------
# decide and order_for
# ---------------------------------------------------------------------------


def test_decide_returns_an_entry_on_the_cross_and_the_gate_allows_it() -> None:
    state = portfolio_state(_balance(), [], _session())
    bars = _bars(RISING_LATE[: CROSS_BAR + 1])
    intent, verdict = decide(_definition(), PARAMS, bars, state, _limits())
    assert intent.action is Action.ENTER_LONG
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 95.0


def test_decide_passes_the_intent_through_the_gate() -> None:
    state = portfolio_state(_balance(), [], _session())
    bars = _bars(RISING_LATE[: CROSS_BAR + 1])
    intent, verdict = decide(_definition(), PARAMS, bars, state, _limits(kill_switch=True))
    assert intent.action is Action.ENTER_LONG
    assert verdict.verdict is Verdict.REJECT


def test_an_allowed_entry_is_sized_from_equity_and_the_close() -> None:
    intent = Intent(action=Action.ENTER_LONG, size_pct=95.0)
    # Cash is deliberately NOT equal to equity: an implementation sizing from
    # cash would give 10 units here, and with the two equal it would have passed.
    balance = _balance(equity=10_000.0, cash=4_000.0)
    order = order_for(intent, _allow(50.0), balance, [], TICKER, 200.0, "r")
    assert order is not None
    assert (order.side, order.ticker, order.client_ref) == (Side.BUY, TICKER, "r")
    # 50% of 10,000 of EQUITY is 5,000 of notional; at 200 a unit that is 25 units.
    assert order.quantity == 25.0


def test_an_entry_while_already_long_sends_nothing() -> None:
    intent = Intent(action=Action.ENTER_LONG, size_pct=95.0)
    held = [Position(ticker=TICKER, quantity=3.0, average_price=1.0, market_value=600.0)]
    assert order_for(intent, _allow(95.0), _balance(), held, TICKER, 200.0, "r") is None


def test_an_exit_sells_the_whole_position() -> None:
    intent = Intent(action=Action.EXIT_LONG)
    held = [Position(ticker=TICKER, quantity=3.0, average_price=1.0, market_value=600.0)]
    order = order_for(intent, _allow(0.0), _balance(), held, TICKER, 200.0, "r")
    assert order is not None
    assert (order.side, order.quantity) == (Side.SELL, 3.0)


def test_an_exit_while_flat_sends_nothing() -> None:
    assert (
        order_for(Intent(action=Action.EXIT_LONG), _allow(0.0), _balance(), [], TICKER, 1.0, "r")
        is None
    )


def test_a_hold_sends_nothing_even_with_a_position() -> None:
    held = [Position(ticker=TICKER, quantity=3.0, average_price=1.0, market_value=600.0)]
    assert (
        order_for(Intent(action=Action.HOLD), _allow(0.0), _balance(), held, TICKER, 1.0, "r")
        is None
    )


def test_a_rejection_sends_nothing() -> None:
    intent = Intent(action=Action.ENTER_LONG, size_pct=95.0)
    reject = RiskVerdict(verdict=Verdict.REJECT, reason="kill switch is set", limited_size_pct=0.0)
    assert order_for(intent, reject, _balance(), [], TICKER, 1.0, "r") is None


def test_an_allowance_of_zero_on_an_entry_sends_nothing() -> None:
    # An allow is never read as "there is an order to send" without the size
    # beside it - and it is the VERDICT's size that decides, so the intent here
    # asks for 95 while the gate permits nothing. An implementation reading
    # intent.size_pct instead would send an order, and with both at zero it
    # would have passed.
    intent = Intent(action=Action.ENTER_LONG, size_pct=95.0)
    assert order_for(intent, _allow(0.0), _balance(), [], TICKER, 1.0, "r") is None


def test_a_position_in_another_ticker_does_not_block_an_entry() -> None:
    intent = Intent(action=Action.ENTER_LONG, size_pct=95.0)
    other = [Position(ticker="OTHER", quantity=1.0, average_price=1.0, market_value=1.0)]
    assert order_for(intent, _allow(10.0), _balance(), other, TICKER, 100.0, "r") is not None


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_bad_reference_price_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="positive, finite price"):
        order_for(Intent(action=Action.HOLD), _allow(0.0), _balance(), [], TICKER, bad, "r")


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def test_a_record_carries_the_verdict_and_round_trips_through_the_log(tmp_path: Path) -> None:
    state = portfolio_state(_balance(), [], _session())
    bars = _bars(RISING_LATE[: CROSS_BAR + 1])
    intent, verdict = decide(_definition(), PARAMS, bars, state, _limits(max_position_pct=10.0))
    rec = record(
        intent,
        verdict,
        bar_timestamp=bars.index[-1].to_pydatetime(),
        recorded_at=NOW,
        source="live",
        ticker=TICKER,
        params=PARAMS,
        protocol_version=1,
    )
    assert rec.requested_size_pct == 95.0
    assert rec.approved_size_pct == 10.0
    assert rec.risk_verdict == "allow"
    assert "max_position_pct" in rec.risk_reason
    path = tmp_path / "log.jsonl"
    write_decisions([rec], path)
    assert read_decisions(path) == [rec]


# ---------------------------------------------------------------------------
# replay and compare
# ---------------------------------------------------------------------------


def _live_log(bars: pd.DataFrame, limits: RiskLimits | None = None) -> list[DecisionRecord]:
    """Decide every evaluable bar as the worker would, one bar at a time."""
    out: list[DecisionRecord] = []
    state = portfolio_state(_balance(), [], _session())
    for i in range(FIRST_EVALUABLE, len(bars) + 1):
        window = bars.iloc[:i]
        intent, verdict = decide(_definition(), PARAMS, window, state, limits or _limits())
        out.append(
            record(
                intent,
                verdict,
                bar_timestamp=window.index[-1].to_pydatetime(),
                recorded_at=NOW,
                source="live",
                ticker=TICKER,
                params=PARAMS,
                protocol_version=1,
            )
        )
    return out


def test_a_faithful_replay_diverges_nowhere() -> None:
    bars = _bars(RISING_LATE)
    live = _live_log(bars)
    replayed = replay(_definition(), PARAMS, bars, live, NOW)
    report = compare(live, replayed)
    assert report.compared == len(live) == len(RISING_LATE) - FIRST_EVALUABLE + 1 == 7
    assert [r.action for r in live].count(Action.ENTER_LONG) == 1
    assert report.divergences == ()
    assert report.missing_in_replay == ()
    assert report.diverging_bars == 0


def test_the_replay_is_a_backtest_record_that_did_not_run_the_gate() -> None:
    bars = _bars(RISING_LATE)
    live = _live_log(bars, _limits(kill_switch=True))
    replayed = replay(_definition(), PARAMS, bars, live, NOW)
    assert {r.source for r in replayed} == {"backtest"}
    assert {r.risk_verdict for r in replayed} == {"allow"}
    assert {r.risk_reason for r in replayed} == {REPLAY_REASON}
    # The live log was fully refused and the replay was not: that is by
    # construction, and compare() must not report it.
    assert compare(live, replayed).divergences == ()


def test_a_revised_price_shows_up_as_a_divergence_on_that_bar() -> None:
    bars = _bars(RISING_LATE)
    live = _live_log(bars)
    revised = bars.copy()
    # The vendor revised the cross bar's close from 99 to 80: sma3 becomes 90.0
    # against sma5 of 92.2, so the cross no longer happens there and that bar's
    # live ENTER_LONG is a replay HOLD. The cross moves to two bars later (sma3
    # 97.0 over sma5 96.2), so a second bar diverges in action too; every later
    # window includes the revised bar, so their indicator values all move. Nothing
    # BEFORE the revised bar can diverge, and that is the assertion that matters.
    revised.loc[revised.index[CROSS_BAR], "close"] = 80.0
    report = compare(live, replay(_definition(), PARAMS, revised, live, NOW))
    cross_stamp = bars.index[CROSS_BAR].to_pydatetime()
    on_cross = {d.field for d in report.divergences if d.bar_timestamp == cross_stamp}
    assert {"action", "indicator_values", "conditions_met", "conditions_failed"} <= on_cross
    assert min(d.bar_timestamp for d in report.divergences) == cross_stamp
    assert report.diverging_bars == len(RISING_LATE) - CROSS_BAR


def test_a_live_bar_absent_from_the_bars_is_reported_not_guessed() -> None:
    bars = _bars(RISING_LATE)
    live = _live_log(bars)
    truncated = bars.iloc[:-1]
    report = compare(live, replay(_definition(), PARAMS, truncated, live, NOW))
    assert report.missing_in_replay == (bars.index[-1].to_pydatetime(),)
    assert report.compared == len(live) - 1


def test_a_different_artifact_shows_up_as_a_params_divergence_on_every_bar() -> None:
    bars = _bars(RISING_LATE)
    live = _live_log(bars)
    # A different fast period with the same warm-up, so every live bar is still
    # replayable and the ONLY thing that differs by construction is `params`.
    report = compare(live, replay(_definition(), {"fast": 2, "slow": 5}, bars, live, NOW))
    params_bars = {d.bar_timestamp for d in report.divergences if d.field == "params"}
    assert len(params_bars) == report.compared
    assert report.divergences[0].field == "params"


def test_two_live_records_for_one_bar_are_one_compared_bar_and_the_last_wins() -> None:
    bars = _bars(RISING_LATE)
    live = _live_log(bars)
    duplicate = record(
        Intent(action=Action.HOLD),
        _allow(0.0),
        bar_timestamp=live[-1].bar_timestamp,
        recorded_at=NOW,
        source="live",
        ticker=TICKER,
        params=PARAMS,
        protocol_version=1,
    )
    replayed = replay(_definition(), PARAMS, bars, [*live, duplicate], NOW)
    # The replay too: one record per bar, so the replay log on disk carries no
    # duplicate even though the append-only live log keeps both.
    assert len(replayed) == len(live)
    report = compare([*live, duplicate], replayed)
    # One bar, not two records - and the duplicate HOLD is what the replay also
    # decides on that bar, so the count is what distinguishes bars from records.
    assert report.compared == len(live)


def test_the_risk_fields_are_not_among_the_compared_ones() -> None:
    assert "risk_verdict" not in COMPARED_FIELDS
    assert "risk_reason" not in COMPARED_FIELDS
    assert "approved_size_pct" not in COMPARED_FIELDS
    assert "recorded_at" not in COMPARED_FIELDS
    assert "params" in COMPARED_FIELDS


def test_a_tiny_value_difference_within_tolerance_is_not_a_divergence() -> None:
    bars = _bars(RISING_LATE)
    live = _live_log(bars)
    nudged = bars.copy() * (1 + 1e-12)
    assert compare(live, replay(_definition(), PARAMS, nudged, live, NOW)).divergences == ()
    # And the tolerance is bounded from above: a nudge of 1e-6 relative is a
    # real price change and every indicator value must report it - as may the
    # rendered condition lines, which carry those values at six significant
    # figures. A crossover is scale-invariant, so neither nudge can move an
    # action, and that is the field that must never appear here.
    coarse = bars.copy() * (1 + 1e-6)
    report = compare(live, replay(_definition(), PARAMS, coarse, live, NOW))
    fields = {d.field for d in report.divergences}
    assert "indicator_values" in fields
    assert fields <= {"indicator_values", "conditions_met", "conditions_failed"}
    assert report.diverging_bars == report.compared


# ---------------------------------------------------------------------------
# the daily-loss latch
# ---------------------------------------------------------------------------
#
# The committed limit is 3%. On a session open of 10,000 that is 300: equity of
# 9,700 has reached it and 9,701 has not.


def _limits_3pct() -> RiskLimits:
    return RiskLimits(
        max_position_pct=95.0, max_daily_loss_pct=3.0, max_open_exposure_pct=95.0, kill_switch=False
    )


def test_a_session_starts_untripped() -> None:
    assert _session().daily_loss_tripped is False


@pytest.mark.parametrize(("equity", "tripped"), [(9_700.0, True), (9_701.0, False)])
def test_the_latch_sets_when_the_account_reaches_the_limit(equity: float, tripped: bool) -> None:
    session = _session(10_000.0)
    state = portfolio_state(_balance(equity=equity), [], session)

    assert trip_if_reached(session, state, _limits_3pct()).daily_loss_tripped is tripped


def test_the_latch_is_not_cleared_by_a_recovery() -> None:
    tripped = dataclasses.replace(_session(10_000.0), daily_loss_tripped=True)
    recovered = portfolio_state(_balance(equity=10_500.0), [], tripped)

    assert trip_if_reached(tripped, recovered, _limits_3pct()) == tripped


def test_a_tripped_session_reaches_the_gate_as_a_tripped_state() -> None:
    tripped = dataclasses.replace(_session(10_000.0), daily_loss_tripped=True)

    assert portfolio_state(_balance(), [], tripped).daily_loss_tripped is True
    assert portfolio_state(_balance(), [], _session()).daily_loss_tripped is False


def test_the_latch_survives_a_restart_on_the_same_day(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save_session(dataclasses.replace(_session(10_000.0), daily_loss_tripped=True), path)

    # A later run the same day, with the account back above its open.
    reloaded = load_session(path, date(2026, 9, 12), 10_500.0)

    assert reloaded.daily_loss_tripped is True
    assert reloaded.start_of_day_equity == 10_000.0


def test_a_new_day_starts_untripped(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save_session(dataclasses.replace(_session(10_000.0), daily_loss_tripped=True), path)

    assert load_session(path, date(2026, 9, 13), 9_000.0).daily_loss_tripped is False


def test_a_session_file_written_before_the_latch_existed_reads_as_untripped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"day": "2026-09-12", "sod": 10_000.0}), encoding="utf-8")

    session = load_session(path, date(2026, 9, 12), 9_000.0)

    assert session == Session(day=date(2026, 9, 12), start_of_day_equity=10_000.0)


@pytest.mark.parametrize("flag", ["true", "false", 1, 0, None, [True]])
def test_a_latch_that_is_not_a_boolean_is_read_as_set(tmp_path: Path, flag: object) -> None:
    # FAILS CLOSED. The rest of the file is readable, so the session open is
    # kept; the one field nobody can vouch for is the latch, and a latch that
    # might be set is treated as set. Reading it as an unreadable file instead
    # would start a new session and clear it, which is the unsafe direction -
    # an earlier version did exactly that, and a review caught it.
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps({"day": "2026-09-12", "sod": 10_000.0, "tripped": flag}), encoding="utf-8"
    )

    session = load_session(path, date(2026, 9, 12), 9_000.0)

    assert session == Session(
        day=date(2026, 9, 12), start_of_day_equity=10_000.0, daily_loss_tripped=True
    )


@pytest.mark.parametrize("content", ["[1, 2]", '"a string"', "3", "null", "true"])
def test_a_session_file_that_is_not_an_object_starts_a_new_session(
    tmp_path: Path, content: str
) -> None:
    # Valid JSON, wrong shape. It must be read as unreadable like any other
    # malformed file - not raise, which would take the worker down.
    path = tmp_path / "s.json"
    path.write_text(content, encoding="utf-8")

    session = load_session(path, date(2026, 9, 12), 9_000.0)

    assert session == Session(day=date(2026, 9, 12), start_of_day_equity=9_000.0)


def test_a_limit_reached_on_a_bar_that_asked_for_nothing_still_refuses_the_next_entry() -> None:
    """The whole reason the latch is asked on every run and not only inside the gate.

    The gate looks at the loss only when an intent carries a size. A limit
    reached while the strategy says "hold" would never be seen by it, and an
    entry after the account recovered would go through.
    """
    limits = _limits_3pct()
    session = _session(10_000.0)

    # Run one: down 3.5%, and nothing is being asked for.
    down = portfolio_state(_balance(equity=9_650.0), [], session)
    session = trip_if_reached(session, down, limits)

    # Run two, same day: back at the open, and the strategy wants in.
    recovered = portfolio_state(_balance(equity=10_000.0), [], session)
    entry = Intent(action=Action.ENTER_LONG, size_pct=95.0, conditions_met=("c",))
    verdict = check(entry, recovered, limits)

    assert verdict.verdict is Verdict.REJECT
    assert "earlier in this session" in verdict.reason
