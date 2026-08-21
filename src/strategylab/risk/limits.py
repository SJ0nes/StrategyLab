"""The risk layer: a veto gate sitting between intent and venue.

This layer reads its OWN config file. NOTHING else in the system may modify
those limits - not a search, not a strategy config, not a review process. A risk
limit that a search can relax is not a limit, and the moment it becomes
adjustable it will be adjusted by whatever is optimising against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path

from strategylab.core.types import Intent

DEFAULT_LIMITS_PATH = Path("configs/risk_limits.yaml")


class Verdict(Enum):
    """Whether an intent may proceed to the venue."""

    ALLOW = "allow"
    REJECT = "reject"


@dataclass(frozen=True)
class RiskLimits:
    """Hard limits, read from the risk layer's own config file.

    Attributes:
        max_position_pct: Largest single position as a percentage of account
            equity, so ``95.0`` means ninety-five percent.
        max_daily_loss_pct: Loss within one session, as a positive percentage
            of starting-of-day equity, at which trading stops for the day.
        max_open_exposure_pct: Largest total exposure across all positions as a
            percentage of account equity.
        kill_switch: When ``True``, every intent is rejected regardless of any
            other limit. Set by a human, cleared by a human.
    """

    max_position_pct: float
    max_daily_loss_pct: float
    max_open_exposure_pct: float
    kill_switch: bool = False


@dataclass(frozen=True)
class PortfolioState:
    """What the account currently looks like, as the risk layer needs to see it.

    Attributes:
        as_of: Date the state describes.
        equity: Total account value in account currency units.
        start_of_day_equity: Account value at the session open, in account
            currency units, used to evaluate the daily loss limit.
        open_exposure: Total value of open positions in account currency units.
        realised_pnl_today: Realised profit and loss so far today, in account
            currency units. Negative when losing.
    """

    as_of: date
    equity: float
    start_of_day_equity: float
    open_exposure: float
    realised_pnl_today: float


@dataclass(frozen=True)
class RiskVerdict:
    """The risk layer's answer on one intent.

    Attributes:
        verdict: Whether the intent may proceed.
        reason: Plain-English statement of which limit bound, or that none did.
            Recorded in the decision log for allowed intents as well as
            rejected ones, so the log shows that the gate ran rather than
            leaving it to be inferred.
        limited_size_pct: The size the intent is permitted at, as a percentage
            of equity. Equal to the requested size when nothing bound, lower
            when a limit reduced it, and ``0.0`` on a rejection.
    """

    verdict: Verdict
    reason: str
    limited_size_pct: float


def load_limits(path: Path = DEFAULT_LIMITS_PATH) -> RiskLimits:
    """Read the risk limits from the risk layer's own config file.

    Deliberately a separate file from ``protocol.yaml`` and from anything in
    ``configs/`` that a strategy or search touches, so that no change to a
    strategy can reach these values.

    Args:
        path: Path to the limits file, relative to the repository root.

    Returns:
        The parsed limits.

    Raises:
        FileNotFoundError: If the file is absent. There is no default: running
            without limits must be impossible, not merely discouraged.
        ValueError: If a field is missing, mistyped, or out of range.
    """
    raise NotImplementedError


def check(intent: Intent, state: PortfolioState, limits: RiskLimits) -> RiskVerdict:
    """Decide whether an intent may proceed, and at what size.

    The veto gate between intent and venue. Evaluates the kill switch first,
    then maximum position size, maximum daily loss, and maximum open exposure.
    A limit may reduce a size rather than reject outright; the kill switch and
    the daily loss limit always reject.

    Args:
        intent: What the strategy wants to do.
        state: The account as it currently stands.
        limits: The hard limits, from :func:`load_limits`.

    Returns:
        The verdict, the reason, and the permitted size.

    Raises:
        ValueError: If ``state.start_of_day_equity`` is not positive.
    """
    raise NotImplementedError
