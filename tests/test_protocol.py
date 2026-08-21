"""Tests for the protocol loader.

The unit-boundary tests here are the important ones. A hundred-fold rate error
does not crash and does not look wrong; it just prints costs that are wrong
forever. The only thing that catches it is an assertion on the exact value
crossing the boundary, which is what `test_costs_convert_percent_to_fraction`
is.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from strategylab.protocol import (
    PERCENT_PER_UNIT,
    ProtocolError,
    increment_walk_forward_touches,
    load_protocol,
)

REPO_PROTOCOL = Path("protocol.yaml")

# The committed protocol, used as the base for mutation in the failure cases.
# Kept as a literal rather than read from disk so a test cannot be made to pass
# by editing the pre-registration.
BASE = """\
protocol_version: 1
created: "2026-08-21"

instruments:
  research: "SPY"
  execution: "VUAG.L"

windows:
  in_sample:    {start: "2005-01-01", end: "2018-12-31"}
  walk_forward: {start: "2019-01-01", end: "2024-12-31", fold_months: 12}

costs:
  commission_pct: 0.05
  spread_pct: 0.03
  slippage_pct: 0.05

validation:
  primary_metric: sharpe
  min_sharpe: 1.0
  min_trades_per_fold: 10
  min_trades_pooled: 30
  max_drawdown_ceiling_pct: 25.0
  pass_margin: 0.20

access_log:
  walk_forward_touches: 0
"""


def write_protocol(tmp_path: Path, text: str) -> Path:
    """Write protocol text to a temp file with its line endings preserved.

    Args:
        tmp_path: pytest-provided temporary directory.
        text: Full protocol file contents.

    Returns:
        Path to the written file.
    """
    path = tmp_path / "protocol.yaml"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


# --------------------------------------------------------------------------
# The unit boundary. protocol.yaml stores percentages; CostModel holds
# fractions; load_protocol is the only place the conversion happens.
# --------------------------------------------------------------------------


def test_costs_convert_percent_to_fraction(tmp_path: Path) -> None:
    costs = load_protocol(write_protocol(tmp_path, BASE)).costs
    # 0.05 percent is five basis points is 0.0005 as a fraction.
    assert costs.commission_frac == pytest.approx(0.0005, rel=1e-12)
    assert costs.spread_frac == pytest.approx(0.0003, rel=1e-12)
    assert costs.slippage_frac == pytest.approx(0.0005, rel=1e-12)


def test_conversion_is_exactly_one_hundredth(tmp_path: Path) -> None:
    # Guards the direction of the division. Multiplying by 100 instead of
    # dividing gives 5.0, which is also a "plausible" number.
    costs = load_protocol(write_protocol(tmp_path, BASE)).costs
    assert costs.commission_frac == pytest.approx(0.05 / PERCENT_PER_UNIT, rel=1e-12)
    assert costs.commission_frac < 0.05


def test_commission_min_flat_is_cash_and_is_not_converted(tmp_path: Path) -> None:
    # A cash amount, not a rate. Dividing it by 100 would silently make every
    # small-position commission a hundredth of what the broker charges.
    text = BASE.replace(
        "  slippage_pct: 0.05\n",
        "  slippage_pct: 0.05\n  commission_min_flat: 3.0\n",
    )
    costs = load_protocol(write_protocol(tmp_path, text)).costs
    assert costs.commission_min_flat == 3.0


def test_optional_rates_default_to_zero(tmp_path: Path) -> None:
    costs = load_protocol(write_protocol(tmp_path, BASE)).costs
    assert costs.commission_min_flat == 0.0
    assert costs.fx_conversion_frac == 0.0


# --------------------------------------------------------------------------
# The pass bar is additive and pre-registered.
# --------------------------------------------------------------------------


def test_effective_sharpe_bar_is_additive(tmp_path: Path) -> None:
    validation = load_protocol(write_protocol(tmp_path, BASE)).validation
    assert validation.min_sharpe == 1.0
    assert validation.pass_margin == 0.20
    # Additive, not multiplicative. A multiplicative reading gives 1.20 as well
    # for these two numbers only by coincidence, so check a second pair.
    assert validation.effective_sharpe_bar == pytest.approx(1.20, rel=1e-12)


def test_effective_sharpe_bar_additive_not_multiplicative(tmp_path: Path) -> None:
    text = BASE.replace("min_sharpe: 1.0", "min_sharpe: 2.0").replace(
        "pass_margin: 0.20", "pass_margin: 0.50"
    )
    validation = load_protocol(write_protocol(tmp_path, text)).validation
    assert validation.effective_sharpe_bar == pytest.approx(2.50, rel=1e-12)  # not 3.00


def test_both_trade_floors_are_read(tmp_path: Path) -> None:
    validation = load_protocol(write_protocol(tmp_path, BASE)).validation
    assert validation.min_trades_per_fold == 10
    assert validation.min_trades_pooled == 30


def test_pooled_floor_below_per_fold_floor_is_rejected(tmp_path: Path) -> None:
    text = BASE.replace("min_trades_pooled: 30", "min_trades_pooled: 5")
    with pytest.raises(ProtocolError, match="min_trades_pooled"):
        load_protocol(write_protocol(tmp_path, text))


# --------------------------------------------------------------------------
# Window rules.
# --------------------------------------------------------------------------


def test_windows_parse_with_attribute_access(tmp_path: Path) -> None:
    protocol = load_protocol(write_protocol(tmp_path, BASE))
    assert protocol.windows.in_sample.start == date(2005, 1, 1)
    assert protocol.windows.walk_forward.end == date(2024, 12, 31)
    # fold_months is a fold LENGTH, not a fold count.
    assert protocol.windows.fold_months == 12


def test_overlapping_windows_are_rejected(tmp_path: Path) -> None:
    text = BASE.replace('walk_forward: {start: "2019-01-01"', 'walk_forward: {start: "2018-06-01"')
    with pytest.raises(ProtocolError, match="overlap"):
        load_protocol(write_protocol(tmp_path, text))


def test_backwards_window_is_rejected(tmp_path: Path) -> None:
    text = BASE.replace(
        'in_sample:    {start: "2005-01-01", end: "2018-12-31"}',
        'in_sample:    {start: "2018-12-31", end: "2005-01-01"}',
    )
    with pytest.raises(ProtocolError, match="ends"):
        load_protocol(write_protocol(tmp_path, text))


# --------------------------------------------------------------------------
# Failures name the offending field.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("removed", "expected"),
    [
        ("  commission_pct: 0.05\n", "costs.commission_pct"),
        ("  min_sharpe: 1.0\n", "validation.min_sharpe"),
        ("  min_trades_pooled: 30\n", "validation.min_trades_pooled"),
    ],
)
def test_missing_field_is_named(tmp_path: Path, removed: str, expected: str) -> None:
    with pytest.raises(ProtocolError, match=expected.replace(".", r"\.")):
        load_protocol(write_protocol(tmp_path, BASE.replace(removed, "")))


def test_negative_rate_is_rejected(tmp_path: Path) -> None:
    text = BASE.replace("spread_pct: 0.03", "spread_pct: -0.03")
    with pytest.raises(ProtocolError, match=r"costs\.spread_pct"):
        load_protocol(write_protocol(tmp_path, text))


def test_non_string_ticker_is_rejected(tmp_path: Path) -> None:
    # Without a type check this loads as the string "['SPY']" and fails much
    # later, at the vendor, naming neither the field nor the file.
    text = BASE.replace('research: "SPY"', "research: [SPY]")
    with pytest.raises(ProtocolError, match=r"instruments\.research"):
        load_protocol(write_protocol(tmp_path, text))


def test_missing_file_raises_protocol_error(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError, match="not found"):
        load_protocol(tmp_path / "absent.yaml")


def test_directory_in_place_of_file_raises_protocol_error(tmp_path: Path) -> None:
    # path.exists() is true for a directory, so this reaches the read.
    with pytest.raises(ProtocolError):
        load_protocol(tmp_path)


# --------------------------------------------------------------------------
# The access counter.
# --------------------------------------------------------------------------


def test_increment_preserves_every_other_byte(tmp_path: Path) -> None:
    path = write_protocol(tmp_path, BASE)
    before = path.read_bytes()
    assert increment_walk_forward_touches(path) == 1
    after = path.read_bytes()
    # Byte-for-byte, line endings included. Text-mode I/O would rewrite every
    # LF to CRLF on Windows and this assertion is what catches that.
    assert after == before.replace(b"walk_forward_touches: 0", b"walk_forward_touches: 1")
    assert after.count(b"\r\n") == before.count(b"\r\n")


def test_increment_is_visible_to_a_reload(tmp_path: Path) -> None:
    path = write_protocol(tmp_path, BASE)
    increment_walk_forward_touches(path)
    increment_walk_forward_touches(path)
    assert load_protocol(path).walk_forward_touches == 2


def test_increment_targets_the_access_log_block_only(tmp_path: Path) -> None:
    # A counter of the same name under another key must not be touched.
    text = BASE.replace("access_log:", "other_block:\n  walk_forward_touches: 99\n\naccess_log:")
    path = write_protocol(tmp_path, text)
    assert increment_walk_forward_touches(path) == 1
    assert "walk_forward_touches: 99" in path.read_text(encoding="utf-8")


def test_missing_counter_raises(tmp_path: Path) -> None:
    text = BASE.replace("  walk_forward_touches: 0\n", "")
    with pytest.raises(ProtocolError, match="walk_forward_touches"):
        increment_walk_forward_touches(write_protocol(tmp_path, text))


# --------------------------------------------------------------------------
# The committed pre-registration itself must load.
# --------------------------------------------------------------------------


@pytest.mark.skipif(not REPO_PROTOCOL.exists(), reason="run from the repository root")
def test_repository_protocol_loads() -> None:
    protocol = load_protocol(REPO_PROTOCOL)
    assert protocol.instruments.research == "SPY"
    assert protocol.instruments.execution == "VUAG.L"
    assert protocol.costs.commission_frac == pytest.approx(0.0005, rel=1e-12)
    assert protocol.validation.effective_sharpe_bar == pytest.approx(1.20, rel=1e-12)
