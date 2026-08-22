"""The colour ramp every heatmap in this project uses.

ONE RAMP, AND IT IS PERCEPTUALLY UNIFORM. A rainbow ramp invents boundaries
that are not in the data - the eye reads a hard edge at the cyan-to-green
transition where the underlying values change smoothly. This project's entire
argument is telling a broad plateau from an isolated spike, so a ramp that
manufactures edges destroys the one distinction the figure exists to make.

Viridis is used because it is perceptually uniform, monotonic in lightness, and
legible to the most common forms of colour blindness. It is hand-coded here
rather than imported: the reports are self-contained single files with no CDN
and no build step, and adding a plotting dependency to draw ten stops would be
a poor trade.
"""

from __future__ import annotations

import math

#: Viridis, sampled at ten evenly spaced stops as ``(red, green, blue)``, each
#: channel 0-255.
#:
#: A SAMPLE OF VIRIDIS, NOT VIRIDIS. The reference map is a 256-entry table;
#: this is ten of its anchors with linear interpolation between them. The
#: difference is invisible at the size a grid cell is drawn and the property
#: that matters - lightness rising monotonically, no manufactured edges -
#: survives the sampling. Stated because "viridis" in a docstring should not
#: imply a fidelity the code does not have.
VIRIDIS: tuple[tuple[int, int, int], ...] = (
    (68, 1, 84),
    (72, 40, 120),
    (62, 74, 137),
    (49, 104, 142),
    (38, 130, 142),
    (31, 158, 137),
    (53, 183, 121),
    (109, 205, 89),
    (180, 222, 44),
    (253, 231, 37),
)

#: The one accent beyond the ramp. It marks the selected thing - the winning
#: cell today, the noise ceiling once that exists - and never two things at
#: once. A second accent is the beginning of a legend, and a legend is a sign
#: the figure has stopped being readable.
ACCENT = "#ff5f1f"

#: Fill for a cell whose backtest raised. NOT a blank and NOT a soft grey: a
#: failed cell must read as "this failed" rather than as "nothing here".
#: Burying an honest non-result is the one thing that makes a research tool
#: untrustworthy. The template draws a hatch over this fill as well, so the
#: distinction survives greyscale printing and colour blindness both.
FAILED_FILL = "#3a3a3a"


def sample(position: float) -> str:
    """Return the ramp colour at a position, as a CSS hex string.

    Args:
        position: Where on the ramp to sample, as a FRACTION from 0.0 to 1.0
            inclusive. Not a percentage. Values outside the range are clamped
            rather than refused, because a caller normalising a metric against
            a min and max can land a hair outside through floating point and
            that is not worth an exception.

    Returns:
        The colour as ``#rrggbb``, lowercase.

    Raises:
        ValueError: If ``position`` is not a real number. A ``NaN`` cannot be
            clamped into range - it compares false against every bound - and
            silently colouring it as the ramp's lowest value would paint a
            failed cell as a merely poor one.
    """
    if isinstance(position, bool) or not isinstance(position, (int, float)):
        raise ValueError(f"position is not a number: {type(position).__name__}")
    if math.isnan(position):
        raise ValueError(
            "position is NaN; a cell with no metric must be drawn as failed rather than "
            "coloured at the bottom of the ramp, which would read as a poor result"
        )

    clamped = min(1.0, max(0.0, float(position)))
    # `len - 1` intervals between `len` stops, so position 1.0 lands exactly on
    # the last stop rather than one past it.
    scaled = clamped * (len(VIRIDIS) - 1)
    lower = min(int(scaled), len(VIRIDIS) - 2)
    fraction = scaled - lower
    start = VIRIDIS[lower]
    end = VIRIDIS[lower + 1]
    channels = (round(start[index] + (end[index] - start[index]) * fraction) for index in range(3))
    return "#" + "".join(f"{channel:02x}" for channel in channels)
