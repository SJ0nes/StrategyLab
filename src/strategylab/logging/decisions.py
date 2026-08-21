"""The decision log: one structured record per decision, written at decision time.

Backtest and live emit the IDENTICAL schema. That is the whole point - it is
what makes ``strategylab diff`` possible, and without it "the live worker
behaved differently" is an anecdote rather than a diff.

Records are never reconstructed after the fact. A record rebuilt from prices
after the event records what the strategy WOULD have seen, not what it did see,
and the gap between those two is precisely the thing being measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from strategylab.core.types import Action

DEFAULT_DECISION_LOG_PATH = Path("state/decisions.jsonl")

SCHEMA_VERSION: int = 1


@dataclass(frozen=True)
class DecisionRecord:
    """Everything known at the moment one decision was made.

    Attributes:
        schema_version: Version of this record shape, so a log written months
            ago can still be read.
        bar_timestamp: Timestamp of the bar being evaluated.
        recorded_at: UTC wall-clock time the record was written. Equal to the
            bar timestamp in a backtest and later than it when live, and the
            difference between the two is itself a measurement.
        source: Either ``backtest`` or ``live``. The only field whose value is
            expected to differ between the two logs being diffed.
        ticker: Symbol evaluated.
        action: What the strategy decided.
        indicator_values: Every indicator value the evaluation read, keyed by
            indicator name, in price units.
        thresholds: Every threshold compared against, keyed by the same names.
        conditions_met: Rendering of each condition that passed, in config
            order.
        conditions_failed: The same for each condition that did not pass. A
            hold is only explicable through this field, which is why rejections
            are logged as fully as entries.
        requested_size_pct: Size the strategy asked for, as a percentage of
            equity.
        approved_size_pct: Size the risk layer permitted, as a percentage of
            equity.
        risk_verdict: The risk layer's answer, as its string value.
        risk_reason: Why the risk layer answered as it did.
        params: The resolved parameters in force, so a record is readable
            without its config.
        protocol_version: Version of the protocol in force, so a record is
            traceable to the rules that produced it.
    """

    schema_version: int
    bar_timestamp: datetime
    recorded_at: datetime
    source: str
    ticker: str
    action: Action
    indicator_values: dict[str, float]
    thresholds: dict[str, float]
    conditions_met: tuple[str, ...]
    conditions_failed: tuple[str, ...]
    requested_size_pct: float
    approved_size_pct: float
    risk_verdict: str
    risk_reason: str
    params: dict[str, int]
    protocol_version: int


def write_decision(record: DecisionRecord, path: Path = DEFAULT_DECISION_LOG_PATH) -> None:
    """Append one decision record to the log as a single JSON line.

    Append-only, one JSON object per line, so a log can be read incrementally
    and a crash mid-write costs at most the final record rather than the file.

    Args:
        record: The record to write.
        path: Log file, relative to the repository root. Created along with its
            parent directory if absent.

    Returns:
        None.

    Raises:
        ValueError: If ``record.source`` is neither ``backtest`` nor ``live``,
            or ``record.schema_version`` does not match
            :data:`SCHEMA_VERSION`.
    """
    raise NotImplementedError


def read_decisions(path: Path = DEFAULT_DECISION_LOG_PATH) -> list[DecisionRecord]:
    """Read a decision log back into records.

    Args:
        path: Log file, relative to the repository root.

    Returns:
        Every record in the file, in the order written.

    Raises:
        FileNotFoundError: If the log does not exist.
        ValueError: If a line is malformed or carries an unknown schema
            version.
    """
    raise NotImplementedError
