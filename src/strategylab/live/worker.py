"""The live worker's decisions, kept pure so they are testable without a venue.

ONE BAR PER INVOCATION. ``strategylab run`` is meant to be scheduled once per
session after the close, not left running: it reconciles, evaluates the latest
bar through the same :func:`strategylab.core.strategy.evaluate` the backtester
uses, passes the intent through :func:`strategylab.risk.limits.check`, sends at
most one order, writes one :class:`~strategylab.logging.decisions.DecisionRecord`,
and exits. A worker that exits has no state to lose between bars except what it
writes to disk, and everything it writes to disk is listed below.

EVERYTHING WITH A CLOCK OR A SOCKET IS IN ``cli.py``. This module takes the
bars, the balance, the positions, the date and the time as arguments, so every
function here is deterministic and ``tests/test_worker.py`` exercises it with
none of those things attached.

DISK STATE THE WORKER OWNS: ``state/session.json`` (the session open's equity,
for the daily-loss limit), ``state/decisions-live.jsonl`` (the log), and the
execution instrument's bars in ``data/``, appended so that live data accumulates
into history as CLAUDE.md requires. ``state/positions.json`` belongs to the
venue adapter.

THE REPLAY IS ``evaluate`` OVER THE SAME BARS, NOT THE ENGINE. A decision log
records decisions, and the engine's decisions ARE ``evaluate``'s - the engine adds
fills and an equity curve, neither of which a decision record carries. Replaying
through ``evaluate`` directly is therefore the cleanest statement of "what the
backtest would have decided on this bar", and it is what ``diff`` compares
against. The risk fields are NOT compared: the replay does not consult the gate,
and ``LIMITATIONS.md`` records that as the decision it is.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from strategylab.core.strategy import evaluate
from strategylab.core.types import Action, Intent, Side, StrategyDef
from strategylab.execution.venue import Balance, Order, OrderType, Position
from strategylab.logging.decisions import SCHEMA_VERSION, DecisionRecord
from strategylab.risk.limits import PortfolioState, RiskLimits, RiskVerdict, Verdict, check

#: Where the session open is remembered, relative to the working directory.
DEFAULT_SESSION_PATH = Path("state/session.json")

#: The two logs `diff` compares. The live one is what `run` appends to.
DEFAULT_LIVE_LOG_PATH = Path("state/decisions-live.jsonl")
DEFAULT_REPLAY_LOG_PATH = Path("state/decisions-replay.jsonl")

#: What the replay writes into the three risk fields it does not compute.
REPLAY_VERDICT = Verdict.ALLOW.value
REPLAY_REASON = "replay: the gate was not consulted, by design; see LIMITATIONS.md"

#: Relative tolerance for comparing an indicator value across the two logs. An
#: SMA recomputed from the same bars should agree to the last bit; the tolerance
#: exists so a vendor revising one price in the ninth decimal is reported as a
#: price change and not as a decision change.
VALUE_REL_TOL = 1e-9

#: Fields the diff compares, in the order they are reported. The risk fields are
#: deliberately absent.
COMPARED_FIELDS: tuple[str, ...] = (
    "action",
    "conditions_met",
    "conditions_failed",
    "requested_size_pct",
    "indicator_values",
    "thresholds",
)


@dataclass(frozen=True)
class Session:
    """The session open, as the daily-loss limit needs it.

    Attributes:
        day: The session date.
        start_of_day_equity: Equity at the first observation of that day, in
            account currency units.
    """

    day: date
    start_of_day_equity: float


def load_session(path: Path, today: date, equity: float) -> Session:
    """Return today's session, starting a new one if the file is stale or absent.

    THE FIRST OBSERVATION OF A DAY DEFINES ITS OPEN. A worker that first runs at
    the close records the close as the open, so on that day the mark-to-market
    loss route reads zero by construction. ``LIMITATIONS.md`` records it.

    Args:
        path: The session file.
        today: The session date.
        equity: Current equity in account currency units, used as the open
            when a new session starts.

    Returns:
        Today's session.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        stored = Session(day=date.fromisoformat(raw["day"]), start_of_day_equity=float(raw["sod"]))
        if stored.day == today and math.isfinite(stored.start_of_day_equity):
            return stored
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return Session(day=today, start_of_day_equity=equity)


def save_session(session: Session, path: Path) -> None:
    """Write the session file.

    Args:
        session: The session to record.
        path: The session file; its parent is created if absent.

    Returns:
        None.

    Raises:
        OSError: If the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"day": session.day.isoformat(), "sod": session.start_of_day_equity}) + "\n",
        encoding="utf-8",
    )


def portfolio_state(
    balance: Balance, positions: list[Position], session: Session
) -> PortfolioState:
    """Assemble what the risk layer needs from what the venue reported.

    ``realised_pnl_today`` IS ALWAYS ZERO from this worker: the venue's cash
    endpoint reports a lifetime realised figure, not a session one, and this
    worker keeps no ledger to difference it against. The daily-loss limit is
    therefore reached only by the mark-to-market route here. ``LIMITATIONS.md``
    says so.

    Args:
        balance: The venue's balance.
        positions: The venue's positions.
        session: Today's session.

    Returns:
        The state, exposure summed over positive market values only.
    """
    exposure = sum(max(p.market_value, 0.0) for p in positions)
    return PortfolioState(
        as_of=session.day,
        equity=balance.equity,
        start_of_day_equity=session.start_of_day_equity,
        open_exposure=exposure,
        realised_pnl_today=0.0,
    )


def decide(
    definition: StrategyDef,
    params: dict[str, int],
    bars: pd.DataFrame,
    state: PortfolioState,
    limits: RiskLimits,
) -> tuple[Intent, RiskVerdict]:
    """Evaluate the latest bar and put the intent through the gate.

    Args:
        definition: The parsed strategy.
        params: Its resolved parameters, in bars.
        bars: The execution instrument's bars, ending at the bar to decide on.
        state: The account as the venue reports it.
        limits: The risk limits.

    Returns:
        The intent and the verdict on it.

    Raises:
        ValueError: Propagated from ``evaluate`` and ``check``.
    """
    intent = evaluate(definition, bars, params)
    return intent, check(intent, state, limits)


def order_for(
    intent: Intent,
    verdict: RiskVerdict,
    balance: Balance,
    positions: list[Position],
    ticker: str,
    last_close: float,
    client_ref: str,
) -> Order | None:
    """Turn a permitted intent into the one order it implies, or nothing.

    LONG-ONLY, ONE POSITION, the same model the engine uses. An ``ENTER_LONG``
    while already holding ``ticker`` sends nothing; an ``EXIT_LONG`` while flat
    sends nothing; a ``HOLD`` never sends anything. A rejection sends nothing
    regardless of action, and so does an allowance of zero - which is what a
    zero-size intent receives, so an ``allow`` is never read as "there is an
    order to send" without the size beside it.

    Args:
        intent: What the strategy wants.
        verdict: What the gate said.
        balance: The venue's balance, for sizing.
        positions: The venue's positions, for the one-position rule.
        ticker: The execution instrument, as the protocol names it.
        last_close: The bar's close in the instrument's currency, used as the
            reference price for sizing a buy.
        client_ref: Caller-generated reference for the order.

    Returns:
        The order, or ``None`` when nothing should be sent.

    Raises:
        ValueError: If ``last_close`` is not positive and finite.
    """
    if not math.isfinite(last_close) or last_close <= 0:
        raise ValueError(f"last_close must be a positive, finite price, got {last_close}")
    if verdict.verdict is not Verdict.ALLOW:
        return None
    held = next((p for p in positions if p.ticker == ticker and p.quantity > 0), None)
    if intent.action is Action.ENTER_LONG and verdict.limited_size_pct > 0 and held is None:
        quantity = balance.equity * verdict.limited_size_pct / 100.0 / last_close
        return Order(
            ticker=ticker,
            side=Side.BUY,
            quantity=quantity,
            order_type=OrderType.MARKET,
            client_ref=client_ref,
        )
    if intent.action is Action.EXIT_LONG and held is not None:
        return Order(
            ticker=ticker,
            side=Side.SELL,
            quantity=held.quantity,
            order_type=OrderType.MARKET,
            client_ref=client_ref,
        )
    return None


def record(
    intent: Intent,
    verdict: RiskVerdict,
    *,
    bar_timestamp: datetime,
    recorded_at: datetime,
    source: str,
    ticker: str,
    params: dict[str, int],
    protocol_version: int,
) -> DecisionRecord:
    """Build the decision record for one bar.

    Args:
        intent: What the strategy wanted.
        verdict: What the gate said. ``approved_size_pct`` is its permitted
            size, which on a rejection is zero.
        bar_timestamp: The bar, timezone-naive.
        recorded_at: Now, timezone-aware at UTC.
        source: ``live`` or ``backtest``.
        ticker: The instrument evaluated.
        params: The parameters in force, in bars.
        protocol_version: The protocol in force.

    Returns:
        The record, ready for :func:`~strategylab.logging.decisions.write_decision`.
    """
    return DecisionRecord(
        schema_version=SCHEMA_VERSION,
        bar_timestamp=bar_timestamp,
        recorded_at=recorded_at,
        source=source,
        ticker=ticker,
        action=intent.action,
        indicator_values=dict(intent.indicator_values),
        thresholds=dict(intent.thresholds),
        conditions_met=intent.conditions_met,
        conditions_failed=intent.conditions_failed,
        requested_size_pct=intent.size_pct,
        approved_size_pct=verdict.limited_size_pct,
        risk_verdict=verdict.verdict.value,
        risk_reason=verdict.reason,
        params=dict(params),
        protocol_version=protocol_version,
    )


def replay(
    definition: StrategyDef,
    params: dict[str, int],
    bars: pd.DataFrame,
    live: list[DecisionRecord],
    recorded_at: datetime,
) -> list[DecisionRecord]:
    """Re-decide every bar the live log covers, from the bars alone.

    For each live record, ``evaluate`` is given the bars up to and including
    that record's bar - the same window the worker had - and the result is
    written as a ``backtest`` record with the risk fields set to
    :data:`REPLAY_VERDICT` and :data:`REPLAY_REASON`. A live bar absent from
    ``bars`` produces no replay record; :func:`compare` reports it.

    Args:
        definition: The parsed strategy.
        params: Its resolved parameters, in bars.
        bars: The execution instrument's bars, covering the live period and
            the warm-up before it.
        live: The live log, in any order.
        recorded_at: Stamped on every replay record, timezone-aware at UTC.

    Returns:
        One record per live bar found in ``bars``, in the live log's order.

    Raises:
        ValueError: Propagated from ``evaluate``, including for a window
            shorter than the strategy's warm-up.
    """
    replayed = []
    for entry in live:
        stamp = pd.Timestamp(entry.bar_timestamp)
        if stamp not in bars.index:
            continue
        window = bars.loc[:stamp]
        intent = evaluate(definition, window, params)
        verdict = RiskVerdict(
            verdict=Verdict.ALLOW, reason=REPLAY_REASON, limited_size_pct=intent.size_pct
        )
        replayed.append(
            record(
                intent,
                verdict,
                bar_timestamp=entry.bar_timestamp,
                recorded_at=recorded_at,
                source="backtest",
                ticker=entry.ticker,
                params=params,
                protocol_version=entry.protocol_version,
            )
        )
    return replayed


@dataclass(frozen=True)
class Divergence:
    """One field on one bar where the two logs disagree.

    Attributes:
        bar_timestamp: The bar.
        field: The record field, one of :data:`COMPARED_FIELDS`.
        live: The live value, rendered.
        replay: The replay value, rendered.
    """

    bar_timestamp: datetime
    field: str
    live: str
    replay: str


@dataclass(frozen=True)
class DiffReport:
    """What the diff found.

    Attributes:
        compared: How many bars appear in both logs.
        divergences: Every field-level disagreement, in bar order.
        missing_in_replay: Live bars the replay could not decide, because the
            bars frame does not hold them.
    """

    compared: int
    divergences: tuple[Divergence, ...]
    missing_in_replay: tuple[datetime, ...]

    @property
    def diverging_bars(self) -> int:
        """How many compared bars carry at least one divergence."""
        return len({d.bar_timestamp for d in self.divergences})


def _values_differ(a: dict[str, float], b: dict[str, float]) -> bool:
    """Say whether two value mappings disagree beyond :data:`VALUE_REL_TOL`.

    Args:
        a: One mapping.
        b: The other.

    Returns:
        ``True`` if the keys differ or any shared value does.
    """
    if a.keys() != b.keys():
        return True
    return any(not math.isclose(a[k], b[k], rel_tol=VALUE_REL_TOL, abs_tol=0.0) for k in a)


def compare(live: list[DecisionRecord], replayed: list[DecisionRecord]) -> DiffReport:
    """Diff the live log against its replay, bar by bar.

    Compares :data:`COMPARED_FIELDS` and nothing else. ``recorded_at`` differs
    by construction and is itself a measurement; ``source`` differs by
    definition; the three risk fields differ because the replay does not run
    the gate.

    Args:
        live: The live log.
        replayed: The replay, from :func:`replay`.

    Returns:
        The report.
    """
    by_bar = {r.bar_timestamp: r for r in replayed}
    divergences: list[Divergence] = []
    missing: list[datetime] = []
    compared = 0
    for entry in sorted(live, key=lambda r: r.bar_timestamp):
        other = by_bar.get(entry.bar_timestamp)
        if other is None:
            missing.append(entry.bar_timestamp)
            continue
        compared += 1
        for field in COMPARED_FIELDS:
            mine, theirs = getattr(entry, field), getattr(other, field)
            if field in ("indicator_values", "thresholds"):
                differs = _values_differ(mine, theirs)
            elif field == "requested_size_pct":
                differs = not math.isclose(mine, theirs, rel_tol=VALUE_REL_TOL, abs_tol=0.0)
            else:
                differs = mine != theirs
            if differs:
                divergences.append(
                    Divergence(
                        bar_timestamp=entry.bar_timestamp,
                        field=field,
                        live=_render(mine),
                        replay=_render(theirs),
                    )
                )
    return DiffReport(
        compared=compared, divergences=tuple(divergences), missing_in_replay=tuple(missing)
    )


def _render(value: object) -> str:
    """Render a field value for a report line.

    Args:
        value: The value.

    Returns:
        Its rendering: an enum's value, a tuple joined with `` AND ``, or ``str``.
    """
    if isinstance(value, Action):
        return value.value
    if isinstance(value, tuple):
        return " AND ".join(value) or "-"
    return str(value)
