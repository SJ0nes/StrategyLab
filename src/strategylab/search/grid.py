"""Grid expansion.

An exhaustive coarse grid is used rather than an iterative tree search, because
a tree only visits the path it walked while a grid reveals the whole parameter
surface - and the surface is what distinguishes a robust plateau from a lucky
spike. See ``docs/decisions/0001-grid-search-over-tree.md``.
"""

from __future__ import annotations


def expand_grid(search_spec: dict[str, dict[str, float]]) -> list[dict[str, int]]:
    """Enumerate every combination of the declared parameter ranges.

    Args:
        search_spec: Parameter ranges keyed by placeholder name without the
            leading ``$``. Each value carries ``min``, ``max``, and ``step``,
            all in the parameter's own units - bars, for a lookback period.
            Ranges are inclusive of ``min`` and of ``max`` when ``max`` falls
            on a step boundary.

    Returns:
        Every combination, each a mapping from parameter name to value. Order
        is deterministic: parameters in the order given, values ascending. An
        empty spec yields a single empty combination, which is the correct
        answer for a strategy with nothing to search rather than an error.

    Raises:
        ValueError: If a range omits a key, if ``step`` is not positive, or if
            ``max`` is below ``min``.
    """
    raise NotImplementedError
