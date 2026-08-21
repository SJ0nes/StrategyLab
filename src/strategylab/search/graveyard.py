"""The graveyard: an append-only record of every rejected candidate.

Nothing is ever deleted. The rejection count is a finding, not waste - it is
the denominator that makes a survivor mean something, and a project that keeps
only its winners has quietly discarded the evidence needed to judge them.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_GRAVEYARD_PATH = Path("state/graveyard.jsonl")


def record_rejection(
    candidate: dict[str, int],
    metrics: dict[str, float],
    reason: str,
    path: Path = DEFAULT_GRAVEYARD_PATH,
) -> None:
    """Append one rejected candidate to the graveyard.

    Append-only. Existing records are never rewritten or removed, so the file
    is a complete history of what was tried and why it did not survive.

    Args:
        candidate: The rejected parameter combination, mapping name to value.
        metrics: The metrics it achieved, keyed by metric name. Recorded even
            though it was rejected, because "rejected with Sharpe 1.9 on an
            edge cell" and "rejected with Sharpe 0.1" are different findings.
        reason: Why it was rejected, from the engine's fixed vocabulary, such
            as ``edge_winner``, ``degenerate_plateau``, ``below_noise_ceiling``,
            or ``drawdown_ceiling``.
        path: Graveyard file, relative to the repository root. Created along
            with its parent directory if absent.

    Returns:
        None.

    Raises:
        ValueError: If ``reason`` is empty.
    """
    raise NotImplementedError
