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


def reference_name(ref: IndicatorRef | float) -> str:
    """Render one side of a condition as the name a decision log uses for it.

    ONE FUNCTION, THREE CALLERS THAT MUST AGREE: the rendered condition lines in
    :attr:`Intent.conditions_met`; the KEYS of :attr:`Intent.indicator_values`
    and :attr:`Intent.thresholds`; and the loader's refusal of two literals that
    would share a key. It lived in :mod:`strategylab.core.strategy` and the
    loader open-coded its literal branch, which put a second implementation
    beside it - the exact drift this function was made public to prevent. It
    lives here because ``types`` is what both of those modules already import.

    THE PLACEHOLDER IS NOT RESOLVED. A searched period renders as ``sma($fast)``
    rather than ``sma(10)``, because the parsed definition is never rewritten -
    one definition serves every cell of a grid - and the values live in the
    ``params`` mapping that travels with it. A reader resolves the name against
    the parameters in the same record.

    SIX SIGNIFICANT FIGURES IS NOT INJECTIVE over floats, so two distinct
    literals can render alike. That is a real hazard for a mapping keyed by this
    function, and it is refused rather than tolerated - by
    :func:`~strategylab.core.config.load_strategy` for a config file, and by
    :func:`~strategylab.core.strategy.evaluate` for a definition built in code.

    Args:
        ref: The indicator reference, or a bare number in the units of whatever
            it is compared against.

    Returns:
        ``indicator(period)`` for a resolved reference, ``indicator($name)`` for
        an unresolved one, ``indicator`` for one taking no period, and six
        significant figures for a literal.
    """
    if not isinstance(ref, IndicatorRef):
        return f"{ref:.6g}"
    if ref.period is not None:
        return f"{ref.indicator}({ref.period})"
    if ref.period_param is not None:
        return f"{ref.indicator}(${ref.period_param})"
    return ref.indicator


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

    NOT HASHABLE. A frozen dataclass generates ``__hash__`` from its fields and
    a dict is not hashable, so ``hash(intent)`` raises ``TypeError`` - which
    became true silently when the two mappings below were added. Nothing in this
    project hashes one, and ``tests/test_strategy.py`` pins the fact so that
    anyone reaching for an ``lru_cache`` or a set of intents finds out at the
    test rather than at run time. The alternative, a tuple of pairs, would make
    every consumer rebuild the mapping a decision record wants.

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

            BOTH FIELDS DESCRIBE ONE LIST, and which list depends on the
            action. An ``EXIT_LONG`` reports the exit conditions; an
            ``ENTER_LONG`` and a ``HOLD`` report the entry conditions. So an
            ``ENTER_LONG`` carries ``conditions_failed=()`` even though it was
            reached only because an exit condition failed - "nothing failed"
            means nothing in the list being reported failed. The other list's
            outcome is not on the record.
        indicator_values: The indicators behind the conditions THIS INTENT
            REPORTS, keyed by the SAME name those rendered conditions use -
            ``sma($fast)``, ``close`` - and valued in price units at the bar
            evaluated.

            KEYED BY REFERENCE, NOT BY INDICATOR. Two conditions on different
            averages give ``sma($fast)`` and ``sma($slow)``, which do not
            collide; keying by indicator alone would have merged them and lost
            one of the two values. The placeholder is kept rather than resolved
            because that is what the rendered strings say, and the parameters
            that resolve it travel in the same decision record.

            EVERY KEY IS NAMED BY A LINE ON THE SAME RECORD, and that is the
            point of the field: a value can be matched to the condition it
            decided. An earlier version merged in the values from the list that
            was evaluated and NOT reported, which left keys no string on the
            record explained. The narrower claim is the true one - what the
            evaluation read but did not report is not recorded here.
        thresholds: Every LITERAL a condition compared against, keyed by its
            own rendering at six significant figures, in whatever units the
            config author meant - which is why it is not merged into
            ``indicator_values``, whose values are prices.

            EMPTY ONLY BECAUSE THE COMMITTED CONFIG HAPPENS NOT TO USE ONE. The
            loader accepts a bare number as a condition's ``target`` today, so
            ``close above 400`` is expressible now rather than one change away;
            an earlier version of this docstring said the vocabulary could not
            express it, and that was a claim about the loader the loader does
            not support.

            Two literals that render identically at six significant figures
            would share a key, and that is REFUSED rather than tolerated - in
            two places, deliberately. A config file meets
            :func:`~strategylab.core.config.load_strategy`, which is where
            validation is declared complete; a definition built in code meets
            :func:`~strategylab.core.strategy.evaluate`, which is the backstop.
            The loader is the one that matters in practice, because the
            evaluator inspects the entry list only on a bar where no exit
            condition holds.
    """

    action: Action
    size_pct: float = 0.0
    conditions_met: tuple[str, ...] = ()
    conditions_failed: tuple[str, ...] = ()
    indicator_values: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
