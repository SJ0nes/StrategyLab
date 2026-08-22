"""Grid expansion.

An exhaustive coarse grid is used rather than an iterative tree search, because
a tree only visits the path it walked while a grid reveals the whole parameter
surface - and the surface is what distinguishes a robust plateau from a lucky
spike. See ``docs/decisions/0001-grid-search-over-tree.md``.

WHAT THIS MODULE IS FOR, STATED WITHOUT OVERCLAIMING. ``load_strategy`` returns a
:class:`~strategylab.core.types.StrategyDef` whose periods may still be
``$fast``, and :func:`~strategylab.core.strategy.evaluate` needs a ``params``
mapping of resolved integers to look each placeholder up in.
:func:`expand_grid` produces those mappings, and so is the piece the two halves
were missing. It is NOT yet the piece that joins them in the product: nothing
under ``src/`` calls this function, and ``cli.search`` still raises before
reading its argument. What exercises the join today is ``tests/test_grid.py``,
which loads the committed config, expands it, and backtests every cell.

This module never receives a ``StrategyDef`` and never rewrites one - it takes
the ``search`` block alone. That is deliberate rather than incidental: one
parsed definition serves every cell of the grid, because the substitution
happens per bar inside ``evaluate`` and not once per cell here.

WHY EVERY BOUND BECOMES AN ``int`` BEFORE ANY RANGE ARITHMETIC. The declared
bounds arrive as floats, because that is what YAML gives back and what
``load_strategy`` carries. The cells are then computed as ``min + k * step``,
which is a single multiply and add - so this is NOT a guard against error
accumulating across a walk, which that formula would not do anyway. It is the
inclusive endpoint that is at risk: whether the last cell exists at all is
decided by comparing an accumulated float against ``max``, and one ulp either
way silently adds or drops a whole parameter combination. On integers that
comparison is exact. The declared return type is ``int`` in any case, and the
cost of the choice is that a non-whole bound is refused rather than rounded.

Validation here is total, in the same sense and for the same reason as
:mod:`strategylab.core.config`: a spec this function accepts must be one every
cell of which is a lookback the engine can actually take. In normal operation
``load_strategy`` has already enforced all of it, and a caller that built a spec
by hand - a test, or a later search component - gets the same answer rather than
a confusing failure three modules away.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

_RANGE_KEYS = frozenset({"min", "max", "step"})


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


def expand_grid(search_spec: dict[str, dict[str, float]]) -> list[dict[str, int]]:
    """Enumerate every combination of the declared parameter ranges.

    Args:
        search_spec: Parameter ranges keyed by placeholder name without the
            leading ``$``. Each value carries ``min``, ``max``, and ``step``,
            and nothing else, every one of them a whole number of bars of at
            least one. Ranges are inclusive of ``min``, and of ``max`` when
            ``max`` falls on a step boundary.

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
        Each mapping is a fresh dict the caller may keep or mutate.

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
            reported: parameters are checked in the order given, and within a
            parameter the checks run as listed here. All three bounds are read
            and converted BEFORE any of them is compared to another, so
            ``{"min": 5.5, "max": 1.5, "step": 1}`` is reported as a non-whole
            bound rather than as a max below its min.

            1. A key that is not a string, or that carries the leading ``$``
               this function's ``Args`` says to strip. ``evaluate`` matches a
               placeholder by its bare name, so ``$fast`` here would surface as
               a missing-parameter error from another module.
            2. A range that omits ``min``, ``max`` or ``step``, or that carries
               any other key. An unrecognised key is a misspelling, and a
               silently ignored one is a range that is not the range its author
               wrote.
            3. A bound that is a ``bool``, or that is neither an ``int`` nor a
               ``float``, or that is not a finite whole number.
            4. A ``step`` below 1.
            5. A ``max`` below its ``min``.
            6. A ``min`` below 1, which with the two checks above is what makes
               every cell at least a one-bar lookback.

            Conditions 1, 2, 3 and 6 are additions to the contract this function
            carried as a stub, which named only a missing key, a non-positive
            step, and a max below min. Each closes a case where the alternative
            was a silently wrong grid rather than a refused one, and together
            they make the guarantee here equal to - rather than weaker than -
            the one ``load_strategy`` enforces on a config file.
    """
    axes: list[list[int]] = []
    for param, spec in search_spec.items():
        if not isinstance(param, str) or param.startswith("$"):
            raise ValueError(
                f"grid parameter {param!r} must be a plain name without the leading '$'"
            )
        unknown = sorted(str(key) for key in spec if key not in _RANGE_KEYS)
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

        # Reported with the DECLARED value, not the converted one, so the number
        # in the message is the number in the operator's file.
        if step < 1:
            raise ValueError(
                f"range for '{param}' has a 'step' of {spec['step']}; must be at least 1 bar"
            )
        if high < low:
            raise ValueError(
                f"range for '{param}' has a 'max' below its 'min' ({spec['max']} < {spec['min']})"
            )
        if low < 1:
            raise ValueError(
                f"range for '{param}' has a 'min' of {spec['min']}; a lookback is at least 1 bar"
            )

        axes.append([low + k * step for k in range((high - low) // step + 1)])

    combinations = itertools.product(*axes)
    return [dict(zip(search_spec, values, strict=True)) for values in combinations]
