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

# Anchored to this file, not to the process working directory. A cwd-relative
# path plus a skipif marker means the one test of the COMMITTED
# pre-registration quietly disappears when pytest runs from elsewhere.
REPO_PROTOCOL = Path(__file__).resolve().parent.parent / "protocol.yaml"

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
    # Pins the divisor as a LITERAL 100. Writing the expected side as
    # `0.05 / PERCENT_PER_UNIT` would take the constant from the module under
    # test, so a PERCENT_PER_UNIT of 10 would move both sides together and the
    # assertion could not fail.
    costs = load_protocol(write_protocol(tmp_path, BASE)).costs
    assert PERCENT_PER_UNIT == 100.0
    assert costs.commission_frac == pytest.approx(0.05 / 100.0, rel=1e-12)
    # Bounds that actually exclude the near misses. `< 0.05` alone admits
    # /10 (0.005), so the upper bound has to be tighter than that value.
    assert costs.commission_frac < 0.001  # excludes 5.0, 0.05, and 0.005
    assert costs.commission_frac > 0.0001  # excludes 0.00005 and below


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


def test_increment_is_visible_to_a_reload(tmp_path: Path) -> None:
    path = write_protocol(tmp_path, BASE)
    increment_walk_forward_touches(path)
    increment_walk_forward_touches(path)
    assert load_protocol(path).walk_forward_touches == 2


def test_increment_targets_the_access_log_block_only(tmp_path: Path) -> None:
    # A counter of the same name under another key must not be touched.
    #
    # The decoy carries the SAME value as the real counter. With a different
    # value (say 99) a naive global replace of "walk_forward_touches: 0" would
    # never match the decoy, leave it alone, and pass this test while having no
    # block-targeting logic at all. The decoy has to be indistinguishable from
    # the real counter except by its position in the file.
    text = BASE.replace("access_log:", "other_block:\n  walk_forward_touches: 0\n\naccess_log:")
    path = write_protocol(tmp_path, text)
    assert increment_walk_forward_touches(path) == 1
    after = path.read_text(encoding="utf-8")
    assert "other_block:\n  walk_forward_touches: 0\n" in after
    assert "  walk_forward_touches: 1\n" in after


def test_missing_counter_raises(tmp_path: Path) -> None:
    text = BASE.replace("  walk_forward_touches: 0\n", "")
    with pytest.raises(ProtocolError, match="walk_forward_touches"):
        increment_walk_forward_touches(write_protocol(tmp_path, text))


# --------------------------------------------------------------------------
# The committed pre-registration itself must load.
# --------------------------------------------------------------------------


def test_repository_protocol_loads() -> None:
    protocol = load_protocol(REPO_PROTOCOL)
    assert protocol.instruments.research == "SPY"
    assert protocol.instruments.execution == "VUAG.L"
    assert protocol.costs.commission_frac == pytest.approx(0.0005, rel=1e-12)
    assert protocol.validation.effective_sharpe_bar == pytest.approx(1.20, rel=1e-12)


# --------------------------------------------------------------------------
# Unit hazards with no other coverage.
# --------------------------------------------------------------------------


def test_drawdown_ceiling_stays_a_percentage(tmp_path: Path) -> None:
    # max_drawdown_ceiling_pct carries the same _pct suffix as the cost rates,
    # but it is NOT converted: it stays a percentage end to end. If it were put
    # through the same /100 as the rates, a 25% ceiling would become 0.25% and
    # reject every candidate ever tested, with no error and no clue why.
    validation = load_protocol(write_protocol(tmp_path, BASE)).validation
    assert validation.max_drawdown_ceiling_pct == 25.0


def test_fx_rate_converts_percent_to_fraction(tmp_path: Path) -> None:
    # The same hundred-fold hazard as commission, and previously the only rate
    # whose conversion nothing asserted.
    text = BASE.replace(
        "  slippage_pct: 0.05\n",
        "  slippage_pct: 0.05\n  fx_conversion_pct: 0.15\n",
    )
    costs = load_protocol(write_protocol(tmp_path, text)).costs
    assert costs.fx_conversion_frac == pytest.approx(0.0015, rel=1e-12)


def test_walk_forward_window_divides_into_whole_folds(tmp_path: Path) -> None:
    # protocol.yaml claims this window "yields six folds". Nothing asserted it.
    # 2019-01-01..2024-12-31 is 72 months; at 12 months per fold that is 6.
    windows = load_protocol(write_protocol(tmp_path, BASE)).windows
    span_months = (
        (windows.walk_forward.end.year - windows.walk_forward.start.year) * 12
        + (windows.walk_forward.end.month - windows.walk_forward.start.month)
        + 1
    )
    assert span_months == 72
    assert span_months % windows.fold_months == 0
    assert span_months // windows.fold_months == 6


def test_counter_survives_a_crlf_file(tmp_path: Path) -> None:
    # The regex anchors must tolerate a \r before end-of-line. Without that,
    # both patterns fail on a CRLF file and the counter is reported as missing
    # from a file that plainly contains it - in the one module written to
    # preserve line endings byte for byte.
    path = write_protocol(tmp_path, BASE.replace("\n", "\r\n"))
    before = path.read_bytes()
    assert before.count(b"\r\n") > 0
    assert increment_walk_forward_touches(path) == 1
    after = path.read_bytes()
    assert after == before.replace(b"walk_forward_touches: 0", b"walk_forward_touches: 1")


def test_counter_tolerates_a_trailing_yaml_comment(tmp_path: Path) -> None:
    # protocol.yaml itself tells the operator to reset this counter by hand, so a
    # trailing note on that line is the likeliest edit anyone will make. The
    # comment must survive the increment untouched.
    text = BASE.replace("  walk_forward_touches: 0", "  walk_forward_touches: 0  # reset by hand")
    path = write_protocol(tmp_path, text)
    assert increment_walk_forward_touches(path) == 1
    assert "  walk_forward_touches: 1  # reset by hand" in path.read_text(encoding="utf-8")
    assert load_protocol(path).walk_forward_touches == 1


def test_access_log_header_tolerates_a_trailing_comment(tmp_path: Path) -> None:
    text = BASE.replace("access_log:", "access_log:  # every read erodes the split")
    path = write_protocol(tmp_path, text)
    assert increment_walk_forward_touches(path) == 1
    assert "# every read erodes the split" in path.read_text(encoding="utf-8")


def test_a_hash_without_preceding_space_is_not_a_comment(tmp_path: Path) -> None:
    # YAML needs whitespace before `#` for a comment to start, so `0#note` is the
    # plain scalar "0#note" and load_protocol rejects it. The incrementer must
    # refuse it too: a pattern looser than the loader would let the counter be
    # rewritten on a line the loader cannot read.
    text = BASE.replace("  walk_forward_touches: 0", "  walk_forward_touches: 0#note")
    path = write_protocol(tmp_path, text)
    with pytest.raises(ProtocolError):
        load_protocol(path)
    with pytest.raises(ProtocolError, match="walk_forward_touches"):
        increment_walk_forward_touches(path)
