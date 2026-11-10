"""The risk layer: a veto gate sitting between intent and venue.

This layer reads its OWN config file. NOTHING else in the system may modify
those limits - not a search, not a strategy config, not a review process. A risk
limit that a search can relax is not a limit, and the moment it becomes
adjustable it will be adjusted by whatever is optimising against it.

UNITS: every number here is a PERCENTAGE and stays one. The single
percent-to-fraction conversion in this project belongs to ``load_protocol`` and
applies to the rates under ``costs:``; nothing in this module multiplies a price,
so nothing in it is a rate. :attr:`RiskLimits.max_position_pct` is compared
against :attr:`~strategylab.core.types.Intent.size_pct`, which is also a
percentage of equity, and converting either side would break the comparison.
Cash amounts - equity, exposure, realised profit and loss - are in account
currency units and are converted to percentages here, where they are compared.

THIS MODULE IMPORTS NO OTHER LOADER, deliberately, and that is why it carries its
own duplicate-key YAML loader rather than importing the identical one from
:mod:`strategylab.core.config`. The rule above is that no change to a strategy
can reach these values; an import would make a change to the strategy loader a
change to the risk layer's parsing, which is the coupling the rule forbids. The
duplicated code is the seventeen-line body of one method, and paying it twice is
the point. An earlier version of this paragraph called it nine lines, which was
a guess rather than a count.

ONE CALLER, WHICH HAS NEVER RUN. :mod:`strategylab.live.worker` passes every
live intent through :func:`check`; the backtester does not consult it - a
backtest is a research measurement of a strategy, not a simulation of an account
with a kill switch on it. ``strategylab run`` has never been invoked against the
venue, so every verdict this module has produced was produced by a test.
``logging/decisions.py``
already carries the three fields this module's verdict fills, and ALL THREE ARE
RENAMED on the way, none of it enforced by anything: ``verdict`` is an enum and
``risk_verdict`` is its ``.value``; ``limited_size_pct`` is ``approved_size_pct``
there; and ``reason`` is ``risk_reason``. An earlier version of this paragraph
counted two, which is the kind of arithmetic a wiring commit inherits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from strategylab.core.types import Intent

# Relative to the WORKING DIRECTORY, not to this file - the same convention as
# `protocol.DEFAULT_PROTOCOL_PATH`, because both are operator-facing defaults for
# a command run from the repository root. A test that must not depend on the
# working directory passes an explicit path.
DEFAULT_LIMITS_PATH = Path("configs/risk_limits.yaml")

# The closed schema of the limits file. A misspelled key is the failure this
# exists for: `kill_swich: true` BESIDE AN OTHERWISE COMPLETE FILE would load as
# a valid set of limits with the switch silently off. A file holding only the
# misspelling is refused by this schema too, and by it FIRST - the unknown-key
# test runs before any field is read, so such a file is refused for the key it
# does not recognise rather than for the three percentages it is missing.
_LIMIT_KEYS = frozenset(
    {
        "max_position_pct",
        "max_daily_loss_pct",
        "max_open_exposure_pct",
        "kill_switch",
    }
)

# Upper bound on every limit, wherever it came from - the file, through
# `_percentage`, and a constructor, through `RiskLimits.__post_init__`. It bounded
# only the file until the type gained an invariant of its own. For the two size
# limits the reason is leverage: a position above one hundred percent of equity is
# borrowing, which this project's cost model and engine do not describe - the
# engine sizes a position out of cash it holds. For `max_daily_loss_pct` the
# leverage argument says nothing and the bound is merely the widest meaningful
# one: a session loss of a hundred percent is the whole account. Note that a
# threshold beyond it would not be strictly unreachable - nothing bounds
# `realised_pnl_today` by `start_of_day_equity`, so a realised loss of 160% of the
# session open is a state this module would accept and measure - it is the
# mark-to-market route that cannot exceed 100, because `check` requires a positive
# equity.
PERCENT_CEILING = 100.0

# The number of percent in a whole, used to turn a ratio of two cash amounts into
# a percentage. SEPARATE FROM THE CEILING ABOVE, which happens to be the same
# number and means something entirely different: one is the largest limit this
# module accepts, the other is a unit conversion. They were one constant until an
# audit pointed out that a symbol doing both jobs is exactly how a units error
# hides in this project. The name matches `protocol.PERCENT_PER_UNIT`, which does
# the same arithmetic in the other direction.
PERCENT_PER_UNIT = 100.0


class Verdict(Enum):
    """Whether an intent may proceed to the venue.

    THESE TWO STRINGS ARE PART OF THE DECISION LOG'S SCHEMA. A record's
    ``risk_verdict`` is documented in :mod:`strategylab.logging.decisions` as
    "the closed vocabulary of :class:`Verdict`", and it is stored as ``str``, so
    whatever fills it converts with ``.value``. Renaming ``allow`` or ``reject``
    here silently changes what an append-only log means, including logs already
    written. ``tests/test_risk.py`` pins both strings for that reason.
    """

    ALLOW = "allow"
    REJECT = "reject"


@dataclass(frozen=True)
class RiskLimits:
    """Hard limits, read from the risk layer's own config file.

    EVERY PERCENTAGE IS FINITE, ABOVE ZERO AND AT MOST
    :data:`PERCENT_CEILING`, and that is an invariant of the type rather than a
    precondition on its callers - see :meth:`__post_init__`.

    NOTHING IS DEFAULTED, ``kill_switch`` INCLUDED. It carried
    ``= False`` until an audit pointed out that the one omission the type made
    cheap was the permissive one, in the module whose whole subject is refusing
    things. Every construction site now states the switch.

    Attributes:
        max_position_pct: Largest single REQUEST as a percentage of account
            equity, so ``95.0`` means ninety-five percent. It is compared against
            :attr:`~strategylab.core.types.Intent.size_pct` alone and never
            against what is already open, so it does not bound a resulting
            POSITION - that is what ``max_open_exposure_pct`` does, by subtracting
            current exposure first. The two coincide only while the book is empty.
            Measured: with this at 95.0, an exposure ceiling of 100.0 and half the
            account already invested, a request for 95% is allowed at 50% and the
            position ends at 100% of equity. The committed file sets both to 95.0,
            which hides it. This attribute read "largest single position" until an
            audit measured what it actually caps.
        max_daily_loss_pct: Loss within one session, as a positive percentage
            of starting-of-day equity, at which an intent carrying a size is
            refused. NOT A LATCH: :func:`check` holds no state, so it refuses only
            while the state it is handed still reads at or beyond this ceiling,
            and a recovery re-permits. See :func:`check` for why that is a gap
            rather than a design.
        max_open_exposure_pct: Largest total exposure across all positions as a
            percentage of account equity.
        kill_switch: When ``True``, every intent is rejected regardless of any
            other LIMIT - including one that asks for no new exposure, so an open
            position cannot be closed through the gate while it is set. Set by a
            human, cleared by a human. One thing still comes first and is not a
            limit: a ``start_of_day_equity`` that is not positive and finite makes
            :func:`check` raise rather than reject, switch or no switch.
    """

    max_position_pct: float
    max_daily_loss_pct: float
    max_open_exposure_pct: float
    kill_switch: bool

    def __post_init__(self) -> None:
        """Refuse a limit that no comparison can bind on, or that binds wrongly.

        :func:`load_limits` already refuses these values when they come from a
        file, and does it first, so a malformed limits file still gets a message
        naming its key. This guards the OTHER way in, and it is not hypothetical.
        :func:`check` takes the SMALLER of the request and each limit, so:

        - ``nan`` never binds - ``nan < 95.0`` is False - and a limit that cannot
          bind is not a limit. ``_percentage`` argues this exact point about the
          file and the type it built permitted the value anyway.
        - a NEGATIVE limit binds all the way past zero. Measured before this
          guard existed: ``RiskLimits(max_position_pct=-5.0, ...)`` against a
          healthy account returned ``ALLOW`` at ``limited_size_pct=-5.0``, and
          ``-math.inf`` returned ``ALLOW`` at ``-inf``. The first writes into a
          decision log cleanly as an approved order for minus five percent of
          equity; the second is refused by the log, as a non-finite number, at
          the point of writing rather than at the point of deciding.

        The alternative was to state the range in prose and trust every
        constructor. :class:`~strategylab.protocol.Windows` decided against that
        for the same reason, in its own words: "a claim that depends on every
        constructor being called carefully is not a guarantee. Here it is an
        invariant of the type instead."


        Raises:
            ValueError: If any of the three percentages is not finite, or is not
                above zero and at most :data:`PERCENT_CEILING`. It differs from
                the ``ValueError`` :func:`load_limits` raises only in its
                message, deliberately - a bad file is a configuration error and
                this is a programming error, and a caller handling one handles
                both.
        """
        for name, value in (
            ("max_position_pct", self.max_position_pct),
            ("max_daily_loss_pct", self.max_daily_loss_pct),
            ("max_open_exposure_pct", self.max_open_exposure_pct),
        ):
            # Two messages, because one message for both cases told an operator
            # holding a nan about the problems of a zero. The finiteness test is
            # not what does the rejecting - `0.0 < nan` is False under every
            # ordering, so the range test alone would refuse nan and both
            # infinities - it is what lets each value be described accurately.
            if not math.isfinite(value):
                raise ValueError(
                    f"{name} is a percentage and must be finite, got {value}; a nan limit never "
                    "binds at all, because every comparison against it is False, and nor does a "
                    "positive-infinite one - while a negative-infinite one binds without bound, "
                    "past zero"
                )
            if not 0.0 < value <= PERCENT_CEILING:
                raise ValueError(
                    f"{name} is a percentage and must be above 0 and at most "
                    f"{PERCENT_CEILING:g}, got {value}; zero means something different and "
                    "useless in each of the three - a position cap of zero reduces every request "
                    "to an ALLOW at a size of nothing, an exposure ceiling of zero leaves no "
                    "headroom ever, and a loss ceiling of zero stops any day not in profit - and "
                    "if the intention is to refuse everything, that is what kill_switch is for"
                )


@dataclass(frozen=True)
class PortfolioState:
    """What the account currently looks like, as the risk layer needs to see it.

    THIS TYPE VALIDATES NOTHING, unlike :class:`RiskLimits` directly above it. A
    ``nan`` equity or a negative exposure is constructible, and that is deliberate:
    a limit is a policy and a bad one is a programming error, while an account
    state is an OBSERVATION and a live worker can genuinely observe an unreadable
    one. :func:`check` therefore REFUSES on a state it cannot read rather than
    raising - see its docstring for the one field that is the exception, and for
    the three that are not read at all when nothing is being asked for.

    Attributes:
        as_of: Date the state describes. NOT READ BY :func:`check`, which
            compares only the four cash amounts below. Nothing here ties the
            "today" of ``realised_pnl_today`` or the "session open" of
            ``start_of_day_equity`` to a date, so this module cannot tell a stale
            state from a current one - the caller that builds the state owns that,
            and :func:`strategylab.live.worker.portfolio_state` stamps the session
            date here without checking it. The field is carried because a verdict
            worth recording is a verdict with a date on it.
        equity: Total account value in account currency units.
        start_of_day_equity: Account value at the session open, in account
            currency units, used to evaluate the daily loss limit.
        open_exposure: Total value of open positions in account currency units.
        realised_pnl_today: Realised profit and loss so far today, in account
            currency units. Negative when losing.
        daily_loss_tripped: Whether the daily loss limit was reached at any
            earlier point in this session. :func:`check` refuses a sized intent
            while this is set, however far the account has recovered since. THE
            CALLER REMEMBERS IT, not this module: :func:`check` is still a pure
            function of what it is handed, and
            :func:`strategylab.live.worker.trip_if_reached` is what sets this
            from :func:`daily_loss_reached` and keeps it in the session file.
            False by default, which is an account that has not reached the
            limit today - and also one whose caller never looked.
    """

    as_of: date
    equity: float
    start_of_day_equity: float
    open_exposure: float
    realised_pnl_today: float
    daily_loss_tripped: bool = False


@dataclass(frozen=True)
class RiskVerdict:
    """The risk layer's answer on one intent.

    Attributes:
        verdict: Whether the intent may proceed.
        reason: Plain-English statement of which limit bound, or that none did -
            or, on the branches that answer about the account rather than about a
            limit, which FIELD made the question unanswerable. MEANT for the
            decision log, on allowed intents as well as rejected ones, so that a
            log shows the gate ran rather than leaving it to be inferred. No
            record carries one yet: the worker that would write it has never
            run.
        limited_size_pct: The size the intent is permitted at, as a percentage
            of equity. Equal to the requested size when nothing bound, lower
            when a limit reduced it, and ``0.0`` on a rejection.

            AN ALLOWANCE OF ZERO BELONGS ONLY TO A REQUEST FOR ZERO, and it
            rests on TWO guards rather than on anything :func:`check` does
            directly. :meth:`RiskLimits.__post_init__` refuses a limit of zero,
            without which a zero position cap would reduce a sized request to zero
            and return it as an ALLOW - an approved order for nothing. And the
            exposure test rejects at zero headroom rather than allowing, without
            which an exposure sitting exactly on its ceiling would do the same
            thing with every limit non-zero. Three guarantees, one property, and
            none of the three is safe alone.
    """

    verdict: Verdict
    reason: str
    limited_size_pct: float


class _NoDuplicateKeyLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that refuses a mapping with a repeated key.

    PyYAML's default is to keep the last of a repeated key and say nothing. In a
    risk limits file that is the worst available behaviour: a second
    ``kill_switch:`` line silently discards the first, so a file that plainly
    reads ``kill_switch: true`` can disarm the switch four lines further down.

    IT ALSO REFUSES A YAML MERGE KEY, as a side effect rather than by design, and
    the refusal is worth documenting because it is the behaviour an operator
    meets. ``construct_mapping`` below constructs every key node BEFORE
    delegating, and ``<<:`` is still a merge-tagged scalar at that point, which
    ``SafeConstructor`` has no constructor for - so a limits file built out of a
    YAML anchor and a merge arrives at the caller as "is not valid YAML". Refusing
    is the right answer for this file anyway: a limit assembled from an anchor
    defined elsewhere is a limit an operator cannot read off the line it is on.
    Verified by loading such a file, not inferred.
    """

    def construct_mapping(self, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
        """Construct a mapping, rejecting any key that appears more than once.

        Args:
            node: The mapping node being constructed.
            deep: Whether to construct child nodes eagerly, as PyYAML defines it.

        Returns:
            The constructed mapping.

        Raises:
            yaml.constructor.ConstructorError: If a key is repeated.
        """
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError:
                # Unhashable: the base class raises PyYAML's own error for it below.
                continue
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found a duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep)


def _percentage(mapping: dict[str, Any], key: str) -> float:
    """Read one required percentage from the limits mapping and bound it.

    ``.nan`` is spelled plainly enough in YAML to arrive here from a file, and a
    limit that no comparison can bind on is not a limit. The range test alone
    would in fact reject it - ``0.0 < nan`` is False under every ordering, as is
    ``0.0 < -inf``, and ``inf <= 100`` is False - so the explicit finiteness call
    changes no outcome. It is kept because it is the check a reader looks for, and
    because :meth:`RiskLimits.__post_init__` uses the same pair to tell a
    non-finite limit apart from an out-of-range one when naming the problem.

    Args:
        mapping: The parsed limits mapping.
        key: The key to read.

    Returns:
        The value as a percentage, unconverted - ``95.0`` means ninety-five
        percent.

    Raises:
        ValueError: If the key is absent, is not a real number, or is outside
            the range above zero to one hundred inclusive.
    """
    if key not in mapping:
        raise ValueError(f"risk limits: '{key}' is required and is missing")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(
            f"risk limits: '{key}' must be a number of percent, got {type(value).__name__}"
        )
    try:
        number = float(value)
    except OverflowError as exc:
        # PyYAML resolves an arbitrarily long digit string to a Python int, and
        # float() of one too large for a double raises OverflowError - which is
        # not a ValueError and so escaped this function's contract. The catch
        # around yaml.load cannot see it: this conversion happens afterwards.
        raise ValueError(
            f"risk limits: '{key}' is too large in magnitude to hold as a floating-point "
            f"number; a percentage has to lie between 0 and {PERCENT_CEILING:g}, and this is "
            "outside that range by a margin no conversion can even represent"
        ) from exc
    if not math.isfinite(number) or not 0.0 < number <= PERCENT_CEILING:
        raise ValueError(
            f"risk limits: '{key}' is a percentage and must be finite, above 0 and at most "
            f"{PERCENT_CEILING:g}, got {number}. Each bound is refused for its own reason: a "
            "non-finite limit either never binds or binds without bound; zero reduces every "
            "request to nothing, or leaves no headroom, or refuses any day not in profit, "
            "depending which of the three it is; and above a hundred is leverage for the two "
            "size limits and simply wider than a whole account for the loss ceiling. To refuse "
            "everything, set 'kill_switch'"
        )
    return number


def _flag(mapping: dict[str, Any], key: str) -> bool:
    """Read one required boolean from the limits mapping.

    Strictly a YAML boolean. ``kill_switch: "false"`` is a non-empty string and
    so truthy, and ``kill_switch: 0`` is falsy; under a permissive read the
    first arms the switch by accident and the second DISARMS it by accident,
    which is the direction that costs money. Both are refused.

    Args:
        mapping: The parsed limits mapping.
        key: The key to read.

    Returns:
        The value.

    Raises:
        ValueError: If the key is absent or is not a boolean.
    """
    if key not in mapping:
        raise ValueError(f"risk limits: '{key}' is required and is missing")
    value = mapping[key]
    if not isinstance(value, bool):
        raise ValueError(
            f"risk limits: '{key}' must be true or false, got {type(value).__name__} "
            f"({value!r}); a quoted 'false' is a string, and a string is not false"
        )
    return value


def load_limits(path: Path = DEFAULT_LIMITS_PATH) -> RiskLimits:
    """Read the risk limits from the risk layer's own config file.

    Deliberately a separate file from ``protocol.yaml`` and from anything in
    ``configs/`` that a strategy or search touches, so that no change to a
    strategy can reach these values.

    The schema is closed and nothing is defaulted, ``kill_switch`` included: an
    omitted switch would have to be read as off, and a limits file is the one
    place in this project where an omission must not be given a safe-looking
    meaning. All four keys are lines an operator can find and change.

    Percentages are returned UNCONVERTED. See the module docstring.

    Args:
        path: Path to the limits file, relative to the working directory.

    Returns:
        The parsed limits.

    Raises:
        FileNotFoundError: If the file itself is absent. There is no default:
            running without limits must be impossible, not merely discouraged.
            The split is on the exception TYPE rather than on absence, so a path
            whose parent is missing or untraversable - ``NotADirectoryError``,
            ``PermissionError`` - arrives as the ValueError below instead. The
            file is absent in those cases too; what differs is which of the two
            documented exceptions says so.
        ValueError: If a field is missing, mistyped, or out of range - and also
            if the file cannot be read for any reason other than absence, or
            cannot be parsed. The contract a caller handles is two exceptions
            wide, so a permission error or a repeated key arrives as the same
            ValueError a bad number does, with the cause chained.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"risk limits: no file at '{path}'. There is no default set of limits, because "
            "running without limits must be impossible rather than merely discouraged"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"risk limits: file at '{path}' is not valid UTF-8") from exc
    except OSError as exc:
        raise ValueError(f"risk limits: cannot read '{path}': {exc}") from exc

    try:
        # _NoDuplicateKeyLoader is a SafeLoader subclass: no arbitrary construction.
        parsed = yaml.load(text, Loader=_NoDuplicateKeyLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"risk limits: '{path}' is not valid YAML: {exc}") from exc
    except (RecursionError, ValueError, OverflowError) as exc:
        # PyYAML resolves some scalars by CONSTRUCTING them, and a construction
        # that fails raises out of the library rather than as a YAMLError; a file
        # nested past the interpreter's limit raises RecursionError from the
        # composer, which is a RuntimeError. Every one of them is a malformed
        # file, and this module's contract admits exactly one exception for that.
        raise ValueError(f"risk limits: '{path}' could not be parsed: {exc}") from exc

    if parsed is None:
        raise ValueError(f"risk limits: file at '{path}' is empty")
    if not isinstance(parsed, dict):
        raise ValueError(
            f"risk limits: '{path}' must hold a mapping of limits at the top level, got "
            f"{type(parsed).__name__}"
        )
    unknown = sorted(str(key) for key in parsed if key not in _LIMIT_KEYS)
    if unknown:
        raise ValueError(
            f"risk limits: '{path}' carries unknown keys {unknown}; this schema allows only "
            f"{sorted(_LIMIT_KEYS)}"
        )

    return RiskLimits(
        max_position_pct=_percentage(parsed, "max_position_pct"),
        max_daily_loss_pct=_percentage(parsed, "max_daily_loss_pct"),
        max_open_exposure_pct=_percentage(parsed, "max_open_exposure_pct"),
        kill_switch=_flag(parsed, "kill_switch"),
    )


def _reject(reason: str) -> RiskVerdict:
    """Build a rejection, which always permits a size of zero.

    Args:
        reason: Plain-English statement of which limit bound, or which field of
            the account or the intent made the question unanswerable.

    Returns:
        The verdict.
    """
    return RiskVerdict(verdict=Verdict.REJECT, reason=reason, limited_size_pct=0.0)


def _loss_routes(state: PortfolioState) -> tuple[float, float]:
    """Return the day's loss by its two routes, each a PERCENTAGE of the session open.

    Args:
        state: The account. ``start_of_day_equity`` must already be known to be
            positive and finite; the caller checks.

    Returns:
        The realised loss and the mark-down from the session open, in that
        order, each as a positive percentage when the account is losing and a
        negative one when it is ahead. Both divide by ``start_of_day_equity``.
    """
    realised = -state.realised_pnl_today / state.start_of_day_equity * PERCENT_PER_UNIT
    marked = (
        (state.start_of_day_equity - state.equity) / state.start_of_day_equity * PERCENT_PER_UNIT
    )
    return realised, marked


def daily_loss_reached(state: PortfolioState, limits: RiskLimits) -> bool:
    """Say whether the account is at or past the daily loss limit right now.

    The same two routes and the same inclusive comparison :func:`check` applies,
    asked without an intent. It exists so a caller can find out on EVERY run,
    not only on the runs where the strategy happens to ask for a size - a limit
    reached on a day the strategy said "hold" is still reached.

    Args:
        state: The account. ``daily_loss_tripped`` is NOT consulted: this
            reports where the account stands now, and remembering is the
            caller's job.
        limits: The hard limits, as percentages.

    Returns:
        True when either route is at or past ``max_daily_loss_pct``. False when
        the figure behind a route is not finite, because a loss that cannot be
        read has not been shown to be reached; :func:`check` refuses a sized
        intent on such a state by its own rule.

    Raises:
        ValueError: If ``state.start_of_day_equity`` is not positive and finite,
            on the same terms as :func:`check`.
    """
    if not math.isfinite(state.start_of_day_equity) or state.start_of_day_equity <= 0:
        raise ValueError(
            "start_of_day_equity must be a positive, finite amount in account currency units, "
            f"got {state.start_of_day_equity}; it is the denominator of the daily loss limit"
        )
    realised, marked = _loss_routes(state)
    return any(
        math.isfinite(route) and route >= limits.max_daily_loss_pct for route in (realised, marked)
    )


def check(intent: Intent, state: PortfolioState, limits: RiskLimits) -> RiskVerdict:
    """Decide whether an intent may proceed, and at what size.

    The veto gate between intent and venue. Of the four limits, the kill switch is
    evaluated first, then maximum position size, then maximum daily loss, then
    maximum open exposure. A size limit may reduce a size rather than reject
    outright; the kill switch and the daily loss limit always reject, and the
    exposure limit rejects when there is no headroom left to reduce into.

    ONE THING PRECEDES ALL FOUR, AND IT IS NOT A LIMIT. ``start_of_day_equity``
    is validated before the kill switch is read, and a non-positive or non-finite
    one RAISES rather than returning a verdict - so with the switch set and a
    meaningless session open, this function raises instead of refusing. An earlier
    version of this docstring said the kill switch "comes before everything",
    which was false. The order is deliberate: that field is the denominator of the
    daily loss limit, and an account whose session open is zero is a programming
    error in whatever built the state rather than a risk decision to answer
    around.

    WHAT IS GATED IS THE SIZE REQUESTED, NOT THE ACTION. An intent asking for
    nothing - ``HOLD`` and ``EXIT_LONG`` both carry ``size_pct`` of zero - is
    allowed at zero, because no limit expressed as a percentage of equity can
    bind on a request for no exposure, and because closing REDUCES risk. A
    daily-loss limit that blocked a position-closing intent would forbid the one
    action that stops the loss it is measuring. The gate keys off the size rather
    than off the action so that an intent carrying a size it should not - a
    ``HOLD`` asking for fifty percent - meets every limit rather than walking
    through on the strength of its action name.

    SO THE CLAIM IS ABOUT SIZE, NOT ABOUT EXITS. An ``EXIT_LONG`` carrying a
    non-zero ``size_pct`` - which :class:`~strategylab.core.types.Intent` says
    cannot happen and nothing enforces - is gated like any other sized request,
    and a reached daily loss limit WILL refuse it. "An exit is never blocked" is
    the wrong summary of this function. "An intent that asks for no new exposure
    is never blocked, except by the kill switch" is the right one.

    THE KILL SWITCH IS THAT EXCEPTION, AND REFUSES AN EXIT TOO. It precedes the
    zero-size allowance, so while it is set a ``HOLD`` records as a rejection and
    an open position cannot be closed through this gate. That is what a switch a
    human throws should mean - stop sending orders to the venue - and flattening
    the account afterwards is a manual act. The cost is that ``risk_verdict``
    stops distinguishing "refused" from "nothing was asked for" while the switch
    is set; read it beside ``action``, which still does.

    TWO WAYS TO REACH THE DAILY LOSS LIMIT, and both are checked, because each can
    hide the other. A realised loss is banked and an unrealised gain elsewhere
    does not undo it; equity marked down from the session open is a loss the
    account is carrying whether or not anything has been sold. BOTH DIVIDE BY
    ``start_of_day_equity``, never by current equity - dividing a mark-down by
    what is left of the account would overstate it. Either one reaching the
    ceiling refuses an intent carrying a size, and reaching it is enough: the
    comparison is inclusive.

    THE DAILY LOSS LIMIT LATCHES ONLY IF THE CALLER MAKES IT. This function is
    pure in ``(intent, state, limits)``: it holds no flag and remembers no
    previous verdict. What it does is honour one it is handed -
    ``state.daily_loss_tripped`` refuses a sized intent on an account that has
    since recovered to the session open. Setting that field is the caller's
    work: the live worker asks :func:`daily_loss_reached` on every run and keeps
    the answer in its session file, so a limit reached this morning still holds
    this afternoon. A caller that never sets the field gets the old behaviour - a
    worker refused at a realised 12.5% that books a gain back to 1% is permitted
    a sized intent on the very next call - and nothing here can tell which kind
    of caller it has. It lasts a SESSION, not longer: a new day is a new session
    with a new open, and the limit is a daily one.

    THE TWO CEILINGS ARE INCLUSIVE IN OPPOSITE DIRECTIONS, which is coherent but
    has to be said. Reaching ``max_daily_loss_pct`` REJECTS. Reaching
    ``max_open_exposure_pct`` is permitted - a request may be reduced to exactly
    the headroom that brings total exposure onto the ceiling - but adding to a
    book already sitting on it is refused, because the headroom is then zero. You
    may sit at the exposure ceiling; you may not add at it. Exposure is measured
    against CURRENT equity, unlike the two loss routes above: a drawdown makes an
    unchanged position a larger share of the account, and that is the share the
    ceiling is about.

    A STATE THAT IS NOT A SET OF NUMBERS IS REFUSED, NOT RAISED ON - FOR AN INTENT
    THAT ASKS FOR A SIZE. A requested size that is negative or non-finite, an
    equity that is not positive and finite, a negative or non-finite open
    exposure, and a non-finite realised profit and loss each produce a rejection
    naming the field. A blown or unreadable account is a state a live worker can
    genuinely observe, and refusing to trade is the safe answer to it; raising
    would take the worker down on the day it most needs to keep answering. Only
    ``start_of_day_equity`` raises, for the reason given above.

    THE THREE ACCOUNT FIELDS ARE NOT READ AT ALL FOR A ZERO-SIZE INTENT, because
    the allowance above returns first. A ``HOLD`` against an account whose equity
    is ``nan`` is allowed at zero rather than refused, and that is the intended
    answer: nothing is computed from those numbers when nothing is being asked
    for, so an unreadable account is not a reason to refuse to do nothing. The
    size itself is validated for every intent.

    WHEN TWO LIMITS BOTH REDUCE A SIZE, THE REASON NAMES THE ONE THAT SET THE
    FINAL NUMBER. The other reduction leaves no trace. The reason is a statement
    about the size that was permitted, not an audit of every comparison made to
    arrive at it.

    Args:
        intent: What the strategy wants to do. ``size_pct`` is a percentage of
            equity.
        state: The account as it currently stands, its cash amounts in account
            currency units.
        limits: The hard limits, from :func:`load_limits`, as percentages.

    Returns:
        The verdict, the reason, and the permitted size as a percentage of
        equity. On a rejection the permitted size is ``0.0``; on an allowance it
        is at most the requested size, and it is positive whenever the request
        was.

    Raises:
        ValueError: If ``state.start_of_day_equity`` is not positive, or is not
            finite. It is the denominator of the daily loss limit, and a limit
            whose denominator is meaningless is not something to answer around:
            nothing here can tell whether such an account is down three percent
            or thirty.
    """
    if not math.isfinite(state.start_of_day_equity) or state.start_of_day_equity <= 0:
        raise ValueError(
            "start_of_day_equity must be a positive, finite amount in account currency units, "
            f"got {state.start_of_day_equity}; it is the denominator of the daily loss limit"
        )

    if limits.kill_switch:
        return _reject(
            "kill switch is set, so every intent is refused, an exit included, until a human "
            "clears it"
        )

    requested = intent.size_pct
    if not math.isfinite(requested) or requested < 0:
        reason = (
            f"requested size {requested} is not a finite percentage of equity at or above zero, "
            "so no limit can be applied to it"
        )
        return _reject(reason)
    if requested == 0:
        return RiskVerdict(
            verdict=Verdict.ALLOW,
            reason=(
                f"no size requested ({intent.action.value}), so no limit on a percentage of "
                "equity can bind"
            ),
            limited_size_pct=0.0,
        )

    if not math.isfinite(state.equity) or state.equity <= 0:
        return _reject(
            f"account equity {state.equity} is not a positive, finite amount, so a position "
            "sized as a percentage of it cannot be opened"
        )
    if not math.isfinite(state.open_exposure) or state.open_exposure < 0:
        return _reject(
            f"open exposure {state.open_exposure} is not a finite amount at or above zero, so "
            "the exposure limit cannot be evaluated"
        )
    if not math.isfinite(state.realised_pnl_today):
        return _reject(
            f"realised profit and loss {state.realised_pnl_today} is not finite, so the daily "
            "loss limit cannot be evaluated"
        )

    permitted = requested
    reason = "no limit bound"

    if limits.max_position_pct < permitted:
        reason = (
            f"max_position_pct {limits.max_position_pct:g} capped the requested "
            f"{requested:g}% of equity"
        )
        permitted = limits.max_position_pct

    if state.daily_loss_tripped:
        return _reject(
            f"max_daily_loss_pct {limits.max_daily_loss_pct:g} was reached earlier in this "
            "session and stays reached until the session ends, whatever the account has "
            "recovered to since. No intent carrying a size is permitted; one asking for none "
            "still is"
        )
    realised_loss_pct, marked_loss_pct = _loss_routes(state)
    if realised_loss_pct >= limits.max_daily_loss_pct:
        return _reject(
            f"max_daily_loss_pct {limits.max_daily_loss_pct:g} reached: realised loss today is "
            f"{realised_loss_pct:.4g}% of start-of-day equity. No intent carrying a size is "
            "permitted while that holds; one asking for none still is"
        )
    if marked_loss_pct >= limits.max_daily_loss_pct:
        return _reject(
            f"max_daily_loss_pct {limits.max_daily_loss_pct:g} reached: equity is "
            f"{marked_loss_pct:.4g}% below the session open, realised or not. No intent carrying "
            "a size is permitted while that holds; one asking for none still is"
        )

    exposure_pct = state.open_exposure / state.equity * PERCENT_PER_UNIT
    headroom_pct = limits.max_open_exposure_pct - exposure_pct
    if headroom_pct <= 0:
        return _reject(
            f"max_open_exposure_pct {limits.max_open_exposure_pct:g} is fully used: open "
            f"exposure is already {exposure_pct:.4g}% of equity"
        )
    if headroom_pct < permitted:
        reason = (
            f"max_open_exposure_pct {limits.max_open_exposure_pct:g} left {headroom_pct:.4g}% of "
            f"equity of headroom, below the requested {requested:g}%"
        )
        permitted = headroom_pct

    return RiskVerdict(verdict=Verdict.ALLOW, reason=reason, limited_size_pct=permitted)
