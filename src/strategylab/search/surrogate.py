"""Surrogate price series: real bars with the edge taken out and the texture left in.

A surrogate is what the noise ceiling is measured against, so what it preserves
and what it destroys IS the methodology. Preserve too much and the strategy
finds the real edge again, making the ceiling unbeatable. Preserve too little
and the strategy finds nothing, making the ceiling trivial. Either way the
comparison stops meaning anything, and it fails silently: both produce a number.

WHAT IS PRESERVED, deliberately:

- **The return distribution.** Every surrogate return is a real return that
  happened, reordered. Fat tails, skew and the exact set of daily moves survive
  because nothing is drawn from a fitted model.
- **Local autocorrelation and volatility clustering**, inside each block. This
  is the property ``CLAUDE.md`` names: an i.i.d. shuffle destroys exactly what a
  trend strategy trades, "which makes the surrogate an unfairly weak opponent
  and sets the bar far too low".
- **Bar geometry.** Blocks are drawn over bar INDICES, not over returns alone,
  so each bar's own ``open/close``, ``high/close`` and ``low/close`` ratios
  travel with its return. A surrogate bar is a real bar's shape at a new price
  level, rather than a synthesised one.

WHAT IS DESTROYED, which is the point: any structure spanning a block boundary.
The long trend that made a parameter set look good on the real series is cut
wherever two blocks meet, so a strategy can only profit from a surrogate by
finding structure that resampling happened to leave in - which is the definition
of noise for this purpose.

WHY LOG RETURNS. Reconstructing a price series by compounding log returns can
never produce a zero or negative price. That is not tidiness: a non-positive
price reaches ``apply_costs``, which refuses it, and would fail the surrogate's
cells rather than scoring them - quietly turning a ceiling into a smaller
ceiling computed from fewer trials than it claims.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Shortest block this module will resample with, in bars.
#:
#: Two, because a block of one IS the i.i.d. shuffle the project rules forbid -
#: it preserves no autocorrelation at all. The real floor is set by the caller,
#: which derives block length from the grid; this is only a backstop against a
#: degenerate spec.
MIN_BLOCK_BARS = 2


def block_length_for(search_spec: dict[str, dict[str, float]]) -> int:
    """Choose the resampling block length for a grid, in BARS.

    THE LONGEST LOOKBACK THE GRID REACHES. A surrogate is a fair opponent only
    if it can express the structure the strategy trades, and a strategy with a
    two-hundred-bar trend filter cannot see a trend inside a twenty-bar block.
    Blocks shorter than the lookback destroy the very thing being tested, the
    strategy finds nothing on noise, and the ceiling comes out far too easy to
    clear - the same failure as shuffling, arriving more quietly.

    The cost is real and is the reason this is recorded in
    ``docs/decisions/0008``: long blocks mean FEW blocks. Two hundred bars over
    a three-and-a-half-thousand-bar window is about eighteen, so surrogates
    resemble one another and the real series more than a conventional bootstrap
    would. The ceiling is correspondingly less able to explore, which biases it
    UPWARD - a harder bar. Erring towards a harder bar is the right direction
    for this project.

    The conventional moving-block choice of roughly ``n ** (1/3)`` is rejected
    for the reason above. It is the better statistic and the worse experiment.

    Args:
        search_spec: The ranges being searched, mapping parameter name to its
            ``min``/``max``/``step``, values in BARS.

    Returns:
        The block length in bars: the largest ``max`` any range declares, never
        below :data:`MIN_BLOCK_BARS`.

    Raises:
        ValueError: If ``search_spec`` is empty, or if a range omits ``max`` or
            carries one that is not a real number. A block length guessed from a
            malformed spec would silently change the methodology.
    """
    if not search_spec:
        raise ValueError("search_spec is empty; there is no lookback to size a block from")

    longest = 0
    for name, bounds in search_spec.items():
        if "max" not in bounds:
            raise ValueError(f"range '{name}' omits 'max', so its longest lookback is unknown")
        value = bounds["max"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"range '{name}' has a non-numeric 'max': {value!r}")
        longest = max(longest, int(value))
    return max(MIN_BLOCK_BARS, longest)


def block_resample(
    bars: pd.DataFrame, block_bars: int, generator: np.random.Generator
) -> pd.DataFrame:
    """Build one surrogate series from real bars by moving-block resampling.

    Blocks of consecutive BAR INDICES are drawn with replacement from the real
    series and laid end to end until the surrogate is the same length as the
    original. The last block is truncated rather than padded, so the surrogate
    has exactly as many bars as the input and sits on the input's calendar - the
    grid's warm-up arithmetic is then identical on real and surrogate data,
    which it must be for the comparison to be like for like.

    Each drawn bar contributes its own close-to-close LOG RETURN and its own
    intra-bar ratios. Closes are rebuilt by compounding those returns from the
    real series' first close; open, high and low are then placed around each new
    close using the ratios that came with it. Volume is carried across
    unchanged - nothing in this project reads it, and inventing a value would be
    inventing data.

    The FIRST bar of the surrogate is the real first bar, unchanged. It has no
    predecessor and therefore no return, and starting the compounding anywhere
    else would put the surrogate at a different price level from the original
    for no reason.

    Args:
        bars: The real OHLCV bars, indexed by ascending timestamp. Must hold at
            least two rows, since one bar yields no return to resample.
        block_bars: Block length in BARS, at least
            :data:`MIN_BLOCK_BARS` and no longer than the number of returns
            available.
        generator: Seeded random source. Passed in rather than created here so
            the caller owns reproducibility: a ceiling that moves between runs
            cannot be compared against anything.

    Returns:
        A frame with the same index, columns and dtypes as ``bars``, holding a
        surrogate series. Prices are strictly positive by construction.

    Raises:
        ValueError: If ``bars`` holds fewer than two rows, lacks a required
            column, or if ``block_bars`` is below :data:`MIN_BLOCK_BARS` or
            exceeds the number of available returns.
    """
    required = ("open", "high", "low", "close")
    missing = [column for column in required if column not in bars.columns]
    if missing:
        raise ValueError(f"bars lacks required column(s) {missing} for resampling")
    if len(bars) < 2:
        raise ValueError(f"bars holds {len(bars)} row(s); a return needs two")
    if block_bars < MIN_BLOCK_BARS:
        raise ValueError(
            f"block_bars is {block_bars}, below the floor of {MIN_BLOCK_BARS}; a block of "
            "one preserves no autocorrelation and is the i.i.d. shuffle the project rules "
            "forbid"
        )

    closes = bars["close"].to_numpy(dtype="float64")
    returns = np.log(closes[1:] / closes[:-1])
    n_returns = len(returns)
    if block_bars > n_returns:
        raise ValueError(
            f"block_bars is {block_bars} but only {n_returns} returns are available; a "
            "block cannot be longer than the series it is drawn from"
        )

    # Ratios of each bar to its OWN close, so a bar's shape travels with its
    # return. Index i here belongs to return i, which is the move INTO bar i+1.
    shapes = np.column_stack(
        [bars[column].to_numpy(dtype="float64")[1:] / closes[1:] for column in required]
    )

    starts = generator.integers(0, n_returns - block_bars + 1, size=n_returns // block_bars + 1)
    order = np.concatenate([np.arange(start, start + block_bars) for start in starts])[:n_returns]

    rebuilt = np.empty_like(closes)
    rebuilt[0] = closes[0]
    rebuilt[1:] = closes[0] * np.exp(np.cumsum(returns[order]))

    surrogate = bars.copy()
    for position, column in enumerate(required):
        values = surrogate[column].to_numpy(dtype="float64").copy()
        values[1:] = rebuilt[1:] * shapes[order, position]
        surrogate[column] = values
    return surrogate
