"""Value types shared by the backtester, the live worker, and the risk layer.

These are shapes, not behaviour. They live in ``core`` because both the
simulated and the live path speak in them, and a type defined on one side of
that boundary would drift from the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(Enum):
    """Direction of an execution.

    ``BUY`` crosses the spread upward, ``SELL`` downward. There is no
    ``NONE`` member: a bar on which nothing executes produces no fill at all
    rather than a fill with a null side.
    """

    BUY = "buy"
    SELL = "sell"


class Action(Enum):
    """What a strategy wants to do on the bar just evaluated.

    ``HOLD`` is a real answer, not the absence of one, and is logged with the
    same detail as an entry or an exit.
    """

    ENTER_LONG = "enter_long"
    EXIT_LONG = "exit_long"
    HOLD = "hold"


@dataclass(frozen=True)
class IndicatorRef:
    """A reference to one indicator series, as named in a strategy config.

    A period written in a config is either a literal number of bars or a
    ``$placeholder`` the search grid supplies a value for. The two are kept in
    separate fields so that ``period`` is only ever a real lookback: a single
    field holding ``int | str`` would make every reader check which it got, and
    the one that forgot would use the string ``"$fast"`` as a window length.

    Attributes:
        indicator: Name from the engine's fixed vocabulary, such as ``sma`` or
            ``close``.
        period: Lookback in bars, or ``None`` when the indicator takes no period
            (``close``) or when the period is still a placeholder. Never a
            ``$placeholder`` itself.
        period_param: Name of the ``$placeholder`` supplying the period, without
            the leading ``$``, or ``None`` once resolved or never placed. A
            reference carrying this is not yet evaluable; the search grid
            resolves it into ``period`` first.
    """

    indicator: str
    period: int | None = None
    period_param: str | None = None

    def __post_init__(self) -> None:
        """Reject a reference that is both resolved and unresolved at once.

        Raises:
            ValueError: If both ``period`` and ``period_param`` are set.
        """
        if self.period is not None and self.period_param is not None:
            raise ValueError(
                f"indicator '{self.indicator}' has both a period ({self.period}) and a "
                f"placeholder (${self.period_param}); exactly one of the two, or neither"
            )


@dataclass(frozen=True)
class Condition:
    """One condition from a strategy's flat, AND-combined condition list.

    The config format permits no nesting, no OR, and no expressions; a
    condition is always ``<left> <operator> <right>``. Anything more expressive
    is a code change with a test, never a config feature.

    Attributes:
        left: The indicator being tested.
        operator: Comparison from the engine's fixed vocabulary, such as
            ``crosses_above``, ``crosses_below``, or ``above``.
        right: The indicator or literal being tested against.
    """

    left: IndicatorRef
    operator: str
    right: IndicatorRef | float


@dataclass(frozen=True)
class StrategyDef:
    """A parsed strategy definition, as loaded from a file in ``configs/``.

    Attributes:
        name: Identifier for the strategy, unique within ``configs/``.
        version: Schema version of the config file.
        description: Prose statement of the strategy and the mechanism it
            claims to exploit.
        entry: Conditions that must ALL hold to open a position.
        exit: Conditions that must ALL hold to close one.
        search: Parameter ranges the grid walks, keyed by placeholder name
            without the leading ``$``.
        fixed: Parameters set on conventional grounds and never searched.
    """

    name: str
    version: int
    description: str
    entry: tuple[Condition, ...]
    exit: tuple[Condition, ...]
    search: dict[str, dict[str, float]] = field(default_factory=dict)
    fixed: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Intent:
    """What a strategy wants to do on one bar, before risk or execution see it.

    An intent is a request, not a decision. The risk layer may veto it and the
    venue may fail to fill it; neither of those possibilities is known here.

    Attributes:
        action: The requested action.
        size_pct: Requested position size as a percentage of account equity, so
            ``95.0`` means ninety-five percent. Zero when ``action`` is
            ``HOLD`` or ``EXIT_LONG``.
        conditions_met: Human-readable rendering of each condition that passed,
            in config order.
        conditions_failed: The same for each condition that did not pass. A
            ``HOLD`` is explained by this field, which is why rejections are
            worth as much log space as entries.
        indicator_values: Every indicator this evaluation actually read, keyed
            by the SAME name the rendered conditions use - ``sma($fast)``,
            ``close`` - and valued in price units at the bar evaluated.

            KEYED BY REFERENCE, NOT BY INDICATOR. Two conditions on different
            averages give ``sma($fast)`` and ``sma($slow)``, which do not
            collide; keying by indicator alone would have merged them and lost
            one of the two values. The placeholder is kept rather than resolved
            because that is what the rendered strings say, and the parameters
            that resolve it travel in the same decision record.

            ONLY WHAT WAS READ. An ``EXIT_LONG`` returns before the entry
            conditions are evaluated, so its mapping holds the exit
            references and nothing else. The field is a record of the
            evaluation that happened, not of the strategy's whole vocabulary.
        thresholds: Every LITERAL a condition compared against, keyed by its
            own rendering, in whatever units the config author meant. Empty
            whenever every condition compares one indicator against another,
            which is true of every condition the current vocabulary can
            express - so this is empty today and is not dead weight: the
            comparison ``close above 400`` is one config change away, and a
            number compared against is not an indicator value.
    """

    action: Action
    size_pct: float = 0.0
    conditions_met: tuple[str, ...] = ()
    conditions_failed: tuple[str, ...] = ()
    # NOT HASHABLE from here on. A frozen dataclass generates ``__hash__`` from
    # its fields, and a dict is not hashable, so hashing an Intent now raises.
    # Nothing does; this is stated rather than worked around, because the
    # alternative - a tuple of pairs - would make every consumer rebuild the
    # mapping the decision record wants.
    indicator_values: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
