"""Grid expansion.

An exhaustive coarse grid is used rather than an iterative tree search, because
a tree only visits the path it walked while a grid reveals the whole parameter
surface - and the surface is what distinguishes a robust plateau from a lucky
spike. See ``docs/decisions/0001-grid-search-over-tree.md``.

WHAT THIS MODULE IS FOR, STATED WITHOUT OVERCLAIMING. ``load_strategy`` returns a
:class:`~strategylab.core.types.StrategyDef` whose periods are not yet resolved:
such a reference carries ``period=None`` and ``period_param="fast"``, the
placeholder name with its ``$`` already stripped, because no field of a
``StrategyDef`` ever holds the string ``$fast`` itself.
:func:`~strategylab.core.strategy.evaluate` turns that back into a lookback by
looking ``fast`` up in a ``params`` mapping. :func:`expand_grid` produces those
mappings, and so is the piece the two halves were missing. It is NOT yet the
piece that joins them in the product: nothing under ``src/`` calls this
function, and ``cli.search`` still raises before reading its argument. What
exercises the join today is ``tests/test_grid.py``, which loads the committed
config, expands it, and backtests every cell.

This module never receives a ``StrategyDef`` and never rewrites one - it takes
the ``search`` block alone. That is deliberate rather than incidental: one
parsed definition serves every cell of the grid, because the substitution
happens per bar inside ``evaluate`` and not once per cell here.

WHY EVERY BOUND BECOMES AN ``int`` BEFORE ANY RANGE ARITHMETIC. A bound reaching
this function from a config is a ``float``, not because YAML made it one - YAML
returns an ``int`` for an unquoted integer literal, and the committed config is
written that way - but because ``load_strategy`` puts every bound through
``_as_float``. The cells are then computed as ``min + k * step``, which is a
single multiply and add, so this is NOT a guard against error accumulating
across a walk, which that formula would not do anyway. It is the inclusive
endpoint that is at risk: whether the last cell exists at all is decided by
comparing against ``max``, and one ulp either way silently adds or drops a whole
parameter combination. On integers that comparison is exact. The declared return
type is ``int`` in any case, and the cost of the choice is that a non-whole
bound is refused rather than rounded.

VALIDATION IS TOTAL AND HAPPENS BEFORE ANY CELL IS ALLOCATED, in the same sense
and for the same reason as :mod:`strategylab.core.config`. Every parameter is
checked before the first axis is built, so a spec whose first range is enormous
and whose second key is malformed is refused rather than exhausting memory on
its way to the refusal.
"""

from __future__ import annotations

import itertools
import math
import re
from typing import Any

# The same charset as `core.config._NAME_PATTERN`, and the same anchors for the
# same reason: Python's `$` also matches before a single trailing newline, so
# `^...$` would accept "fast\n" - which a YAML block scalar produces without
# anyone meaning to - and that newline would travel into a result key.
_NAME_PATTERN = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_RANGE_KEYS = frozenset({"min", "max", "step"})


def _show(value: object) -> str:
    """Render a value for an error message without the rendering itself raising.

    The case this was written for: CPython refuses to convert an integer of more
    than 4300 digits to a string, raising ``ValueError``. That threshold is the
    DEFAULT of a process-global limit, not a fixed property of the interpreter -
    ``sys.set_int_max_str_digits``, ``-X int_max_str_digits`` and
    ``PYTHONINTMAXSTRDIGITS`` all move it - so the guard is written to catch the
    failure rather than to predict the number.

    An oversized value reaches a message by three routes, not one: as a bound,
    through :func:`_whole_bars`'s int fast path; as a key inside a range; and as
    a parameter name. Only the first passes through ``_whole_bars``.

    The guard is broad on purpose. ``f"{value}"`` dispatches to the value's own
    ``__format__`` and ``__str__``, which for a caller-built spec is arbitrary
    code that may raise anything at all. An error message is the one place where
    losing the original failure to a secondary one is never the better outcome.

    Args:
        value: Any value bound for an error message.

    Returns:
        The value rendered, or a placeholder naming its type when rendering it
        raises.
    """
    try:
        return f"{value}"
    except Exception:  # noqa: BLE001 - see the docstring; the alternative is losing the real error
        return f"<unrenderable {type(value).__name__}>"


def _whole_bars(value: Any, param: str, key: str) -> int:
    """Convert one declared bound to the exact integer number of bars it names.

    An ``int`` is returned unchanged, which is what keeps a very large one from
    reaching :func:`math.isfinite` - that converts to ``float`` and raises
    ``OverflowError`` past about 1.8e308, which is not a ``ValueError`` and would
    escape this function's contract.

    Args:
        value: The declared bound, in bars.
        param: Parameter name, without the leading ``$``, used in the message.
        key: Which bound this is (``min``, ``max`` or ``step``), used in the
            message.

    Returns:
        The bound as an ``int``, exactly equal to ``value``.

    Raises:
        ValueError: If ``value`` is a ``bool``; if it is neither an ``int`` nor
            a ``float`` - which refuses ``Decimal`` and ``numpy.int64``, while
            ``numpy.float64`` is accepted because it subclasses ``float``; or if
            it is a float that is not finite or not whole.
    """
    if isinstance(value, bool):
        raise ValueError(
            f"range for '{param}' has a boolean '{key}': {value!r}. YAML's 'true' is an int "
            "to Python, and a lookback of True is a one-bar lookback nobody asked for"
        )
    if isinstance(value, int):
        return value
    if not isinstance(value, float):
        raise ValueError(
            f"range for '{param}' has a '{key}' that is neither an int nor a float: "
            f"{type(value).__name__}"
        )
    if not math.isfinite(value) or value != int(value):
        raise ValueError(
            f"range for '{param}' has a '{key}' of {value}, which is not a finite whole "
            "number of bars; a grid cell is a lookback and cannot be a fraction of a bar"
        )
    return int(value)


def _bounds(param: str, spec: Any) -> tuple[int, int, int]:
    """Validate one parameter's range and return its bounds as integers.

    Args:
        param: Parameter name, already known to be well formed.
        spec: The declared range, not yet known to be a mapping.

    Returns:
        ``(min, max, step)``, every one a whole number of bars of at least one.

    Raises:
        ValueError: In the order documented on :func:`expand_grid`: a range that
            is not a mapping, carries an unknown key, or omits a required one; a
            bound - taken in the order ``min``, ``max``, ``step`` - that
            :func:`_whole_bars` refuses; a ``step`` below 1; a ``max`` below its
            ``min``; a ``min`` below 1.
    """
    if not isinstance(spec, dict):
        raise ValueError(
            f"range for '{param}' is not a mapping: {type(spec).__name__}. "
            "A range is written {min: ..., max: ..., step: ...}"
        )
    unknown = sorted(_show(key) for key in spec if key not in _RANGE_KEYS)
    if unknown:
        raise ValueError(
            f"range for '{param}' carries unknown keys {unknown}; "
            f"a range allows only {sorted(_RANGE_KEYS)}"
        )
    missing = sorted(key for key in _RANGE_KEYS if key not in spec)
    if missing:
        raise ValueError(
            f"range for '{param}' omits {missing}; a range needs 'min', 'max' and 'step'"
        )

    low = _whole_bars(spec["min"], param, "min")
    high = _whole_bars(spec["max"], param, "max")
    step = _whole_bars(spec["step"], param, "step")

    # Reported with the value as this function received it, rather than the
    # converted int. That is what the caller passed; note it is not necessarily
    # what a config FILE says, because `load_strategy` has already floated every
    # bound, so `min: 0` in YAML arrives here - and is reported - as `0.0`.
    if step < 1:
        raise ValueError(
            f"range for '{param}' has a 'step' of {_show(spec['step'])}; must be at least 1 bar"
        )
    if high < low:
        raise ValueError(
            f"range for '{param}' has a 'max' below its 'min' "
            f"({_show(spec['max'])} < {_show(spec['min'])})"
        )
    if low < 1:
        raise ValueError(
            f"range for '{param}' has a 'min' of {_show(spec['min'])}; a lookback is at least 1 bar"
        )
    return low, high, step


def expand_grid(search_spec: dict[str, dict[str, float]]) -> list[dict[str, int]]:
    """Enumerate every combination of the declared parameter ranges.

    Args:
        search_spec: Parameter ranges keyed by placeholder name without the
            leading ``$``. Each value carries ``min``, ``max``, and ``step``,
            and nothing else, every one of them a whole number of bars of at
            least one. Ranges are inclusive of ``min``, and of ``max`` when
            ``max`` falls on a step boundary.

            The annotation says ``float`` for a bound; an ``int`` is equally
            acceptable and is what the committed config is written in. It says
            ``str`` for a key, and the refusals below still check that at
            runtime, because a spec can be built by hand or come from parsed
            YAML - neither of which a type checker has seen.

            EVERY SEARCHED PARAMETER IS A LOOKBACK, which is why bars are the
            only unit here and why a fractional bound is an error rather than a
            rounding. That is not a restriction this function invents:
            ``load_strategy`` requires every searched parameter to be referenced
            by a condition, and only a period reference can carry a placeholder,
            so a searched parameter that is not a lookback cannot reach this
            function from a config. A threshold or a ratio would need a code
            change, here and in ``core/``, not merely a different bound.

    Returns:
        Every combination, each a mapping from parameter name to value. Order
        is deterministic: parameters in the order given, values ascending, with
        the LAST parameter varying fastest - so the grid reads like an odometer.
        Each mapping is a fresh dict the caller may keep or mutate, and
        ``search_spec`` and the ranges inside it are not modified.

        The count along one axis is ``floor((max - min) / step) + 1``, and the
        length of the result is the product of those counts. A range never
        yields zero cells, because ``max`` is at least ``min`` and so ``min``
        itself is always in it; an empty spec therefore yields a single empty
        combination, which is the right answer for a strategy with nothing to
        search rather than an error.

        BOTH THE AXES AND THE PRODUCT ARE BUILT EAGERLY, and nothing caps
        either. The length formula above is the only warning a caller gets that
        a wide range with a small step is a list it cannot hold; the failure
        mode there is a hang or a ``MemoryError``, not a ``ValueError``. What
        keeps the committed grid affordable is the config, not this function -
        see ``docs/decisions/0001-grid-search-over-tree.md``.

    Raises:
        ValueError: On any of the following. THE ORDER IS PART OF THE CONTRACT,
            because two of them can be true at once and only the first is
            reported. ``search_spec`` is checked first, then each parameter in
            the order given, and within a parameter the checks run as listed.

            1. ``search_spec`` is not a ``dict``. A ``Mapping`` that is not a
               ``dict`` is refused too; the check is by type, not by protocol.
            2. A key that is not a string. Reported separately from, and before,
               the charset check below, so the message names the fault that
               fired.
            3. A key that is not made only of letters, digits, underscores and
               hyphens - the rule ``load_strategy`` applies to the same names,
               and the one that refuses the leading ``$`` this function's
               ``Args`` says to strip.
            4. A range that is not a ``dict``, by type as in item 1.
            5. A range carrying a key other than ``min``, ``max`` and ``step``.
               Unknown keys are reported BEFORE missing ones, so a range with a
               misspelled ``stp`` is named for the misspelling rather than for
               the ``step`` it thereby omits.
            6. A range omitting ``min``, ``max`` or ``step``.
            7. A bound that is a ``bool``, or is neither an ``int`` nor a
               ``float``, or is not a finite whole number. All three bounds are
               converted, in the order ``min``, ``max``, ``step``, BEFORE any
               two of them are compared - so
               ``{"min": 5, "max": 1.5, "step": 1}`` is reported as a non-whole
               ``max``, not as a max below its min. The ``step`` is present in
               that example on purpose: without it item 6 fires first and the
               example never reaches the behaviour it is meant to show.
            8. A ``step`` below 1.
            9. A ``max`` below its ``min``.
            10. A ``min`` below 1, which with the two checks above is what makes
                every cell at least a one-bar lookback.

            Conditions 1 to 7 and 10 are additions to the contract this function
            carried as a stub, which named only a missing key, a non-positive
            step, and a max below min. Each closes a case where the alternative
            was a silently wrong grid, or an exception of a type this contract
            does not admit, rather than a refused one.

            THREE DIFFERENCES REMAIN between what this refuses and what
            ``load_strategy`` refuses, and all three run the same way - accepted
            here, refused there. Every other reason either module rejects a
            searched range is now shared.

            - An integer bound too large to hold as a float. ``load_strategy``
              converts every bound to a float and refuses what will not fit;
              this function never converts, so it accepts one.
            - A searched parameter no condition references. That check needs the
              condition list.
            - A name appearing in both ``search`` and ``fixed``. That check needs
              the ``fixed`` block.

            The last two are not oversights and cannot be closed here: this
            function receives the ``search`` block alone and has nothing to
            cross-check it against.
    """
    if not isinstance(search_spec, dict):
        raise ValueError(
            f"search_spec is not a mapping: {type(search_spec).__name__}. "
            "It maps each parameter name to its range"
        )

    # Every parameter is validated before any axis is allocated, so a refusal
    # never arrives after a large earlier range has already been materialised.
    names = list(search_spec)
    bounds: list[tuple[int, int, int]] = []
    for param in names:
        spec = search_spec[param]
        if not isinstance(param, str):
            raise ValueError(
                f"grid parameter is not a string: {type(param).__name__} "
                f"({_show(param)}); a parameter name becomes part of a result key"
            )
        if not _NAME_PATTERN.match(param):
            raise ValueError(
                f"grid parameter {param!r} must be letters, digits, underscores or "
                "hyphens, without the leading '$'; it becomes part of a result key"
            )
        bounds.append(_bounds(param, spec))

    axes = [[low + k * step for k in range((high - low) // step + 1)] for low, high, step in bounds]
    combinations = itertools.product(*axes)
    # Paired against `names`, the same list the axes were built from, so the
    # pairing does not rest on `search_spec` being iterated twice in one order.
    return [dict(zip(names, values, strict=True)) for values in combinations]
