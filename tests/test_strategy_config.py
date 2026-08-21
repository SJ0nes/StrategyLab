"""Tests for the strategy-config loader.

Two kinds of test live here. The first kind pins the SHAPE the loader produces
from a file, including which periods stay unresolved placeholders. The second,
and the larger, pins what it REFUSES: the declarative rule is only worth
something if a nested target, an expression, an unknown operator, or a searched
parameter nothing references stops the run rather than passing quietly into a
search that takes hours and answers a question nobody asked.

Every rejection test asserts on the dotted field path in the message, because an
error that does not say where is an error the operator has to hunt for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategylab.core.config import (
    INDICATORS,
    OPERATORS,
    SUPPORTED_VERSION,
    StrategyConfigError,
    load_strategy,
)
from strategylab.core.types import IndicatorRef, StrategyDef

# Anchored to this file, not to the process working directory, so the one test
# of the COMMITTED config does not quietly change meaning when pytest is run
# from somewhere else.
REPO_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "ma_crossover.yaml"

# A valid config, held as a literal and mutated per case. Kept separate from the
# committed one so that a test cannot be made to pass by editing `configs/`.
BASE = """\
name: test_strategy
version: 1
description: "Test fixture. Mechanism: none, this file exists to be parsed."

entry:
  - indicator: sma
    period: "$fast"
    operator: crosses_above
    target: {indicator: sma, period: "$slow"}
  - {indicator: close, operator: above, target: {indicator: sma, period: 200}}

exit:
  - indicator: sma
    period: "$fast"
    operator: crosses_below
    target: {indicator: sma, period: "$slow"}

search:
  fast: {min: 5, max: 30, step: 5}
  slow: {min: 40, max: 120, step: 20}

fixed:
  position_size_pct: 95.0
"""

# The exact text of each top-level block in BASE, so a test can remove one whole
# and the removal is checked to have actually happened.
BLOCKS = {
    "name": "name: test_strategy\n",
    "version": "version: 1\n",
    "description": (
        'description: "Test fixture. Mechanism: none, this file exists to be parsed."\n'
    ),
    "entry": (
        "entry:\n"
        "  - indicator: sma\n"
        '    period: "$fast"\n'
        "    operator: crosses_above\n"
        '    target: {indicator: sma, period: "$slow"}\n'
        "  - {indicator: close, operator: above, target: {indicator: sma, period: 200}}\n"
    ),
    "exit": (
        "exit:\n"
        "  - indicator: sma\n"
        '    period: "$fast"\n'
        "    operator: crosses_below\n"
        '    target: {indicator: sma, period: "$slow"}\n'
    ),
}

SECOND_ENTRY = "  - {indicator: close, operator: above, target: {indicator: sma, period: 200}}\n"


def write_config(tmp_path: Path, text: str) -> Path:
    """Write config text to a temp file and return its path.

    Args:
        tmp_path: pytest-provided temporary directory.
        text: Full config file contents.

    Returns:
        Path to the written file.
    """
    path = tmp_path / "strategy.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def mutate(old: str, new: str) -> str:
    """Return BASE with one unique substring replaced.

    The uniqueness check is the point. A mutation that matches nothing produces
    a file identical to BASE, which loads fine - so the rejection test built on
    it would pass while testing nothing at all.

    Args:
        old: Substring to replace, which must appear in BASE exactly once.
        new: Replacement text.

    Returns:
        The mutated config text.
    """
    assert BASE.count(old) == 1, f"mutation target {old!r} appears {BASE.count(old)} times in BASE"
    return BASE.replace(old, new)


# --------------------------------------------------------------------------
# The base config loads. Every rejection test below is built by mutating it,
# so if this fails those tests are all vacuous.
# --------------------------------------------------------------------------


def test_the_base_config_loads(tmp_path: Path) -> None:
    definition = load_strategy(write_config(tmp_path, BASE))
    assert isinstance(definition, StrategyDef)
    assert definition.name == "test_strategy"
    assert definition.version == 1
    assert len(definition.entry) == 2
    assert len(definition.exit) == 1


# --------------------------------------------------------------------------
# What the loader produces.
# --------------------------------------------------------------------------


def test_a_placeholder_period_stays_unresolved(tmp_path: Path) -> None:
    # The grid resolves these, and the grid does not exist yet. What the loader
    # must NOT do is invent a number for them.
    first = load_strategy(write_config(tmp_path, BASE)).entry[0]
    assert first.left == IndicatorRef(indicator="sma", period=None, period_param="fast")
    assert first.operator == "crosses_above"
    assert first.right == IndicatorRef(indicator="sma", period=None, period_param="slow")


def test_a_literal_period_is_kept_as_a_count_of_bars(tmp_path: Path) -> None:
    right = load_strategy(write_config(tmp_path, BASE)).entry[1].right
    assert right == IndicatorRef(indicator="sma", period=200, period_param=None)


def test_an_indicator_without_a_period_carries_neither(tmp_path: Path) -> None:
    left = load_strategy(write_config(tmp_path, BASE)).entry[1].left
    assert left == IndicatorRef(indicator="close", period=None, period_param=None)


def test_a_numeric_target_is_read_as_a_literal(tmp_path: Path) -> None:
    text = mutate(SECOND_ENTRY, "  - {indicator: close, operator: above, target: 30}\n")
    right = load_strategy(write_config(tmp_path, text)).entry[1].right
    assert right == 30.0
    assert isinstance(right, float)


def test_search_and_fixed_are_read_as_given(tmp_path: Path) -> None:
    definition = load_strategy(write_config(tmp_path, BASE))
    assert definition.search == {
        "fast": {"min": 5.0, "max": 30.0, "step": 5.0},
        "slow": {"min": 40.0, "max": 120.0, "step": 20.0},
    }
    assert definition.fixed == {"position_size_pct": 95.0}


def test_search_and_fixed_default_to_empty(tmp_path: Path) -> None:
    # A strategy with nothing to search is a legal strategy, not an error.
    text = BASE
    for block in (
        "search:\n  fast: {min: 5, max: 30, step: 5}\n  slow: {min: 40, max: 120, step: 20}\n",
        "fixed:\n  position_size_pct: 95.0\n",
    ):
        assert text.count(block) == 1
        text = text.replace(block, "")
    text = text.replace('period: "$fast"', "period: 10").replace('period: "$slow"', "period: 50")
    definition = load_strategy(write_config(tmp_path, text))
    assert definition.search == {}
    assert definition.fixed == {}


def test_the_conditions_keep_the_order_the_file_gives(tmp_path: Path) -> None:
    entry = load_strategy(write_config(tmp_path, BASE)).entry
    assert [condition.left.indicator for condition in entry] == ["sma", "close"]


# --------------------------------------------------------------------------
# The committed config. This is the file `uv run strategylab search` will be
# pointed at, so it is worth a test of its own.
# --------------------------------------------------------------------------


def test_the_committed_config_loads_as_the_strategy_it_describes() -> None:
    definition = load_strategy(REPO_CONFIG)
    assert definition.name == "ma_crossover"
    assert definition.version == SUPPORTED_VERSION
    # The folded scalar ends in a newline; the loader strips it.
    assert definition.description.startswith("Trend following.")
    assert not definition.description.endswith("\n")

    assert [(c.left.period_param, c.operator) for c in definition.entry] == [
        ("fast", "crosses_above"),
        (None, "above"),
    ]
    assert [(c.left.period_param, c.operator) for c in definition.exit] == [
        ("fast", "crosses_below"),
    ]
    assert sorted(definition.search) == ["fast", "slow", "trend"]
    assert definition.fixed == {"position_size_pct": 95.0}


def test_the_committed_config_declares_a_ninety_cell_grid() -> None:
    # README and LIMITATIONS both quote 90 cells. That number is a property of
    # this file, so it is checked against this file. Counts computed by hand:
    #   fast   5..30  step 5  -> (30-5)/5  + 1 = 6
    #   slow  40..120 step 20 -> (120-40)/20+1 = 5
    #   trend 100..200 step 50 -> (200-100)/50+1 = 3
    #   6 * 5 * 3 = 90
    search = load_strategy(REPO_CONFIG).search
    counts = {
        name: int((spec["max"] - spec["min"]) // spec["step"]) + 1 for name, spec in search.items()
    }
    assert counts == {"fast": 6, "slow": 5, "trend": 3}
    assert counts["fast"] * counts["slow"] * counts["trend"] == 90


# --------------------------------------------------------------------------
# The file itself.
# --------------------------------------------------------------------------


def test_a_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(StrategyConfigError, match="file not found"):
        load_strategy(tmp_path / "absent.yaml")


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StrategyConfigError, match="is empty"):
        load_strategy(write_config(tmp_path, ""))


def test_malformed_yaml_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StrategyConfigError, match="not valid YAML"):
        load_strategy(write_config(tmp_path, "entry: [\n"))


def test_a_root_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StrategyConfigError, match="'<root>' must be a mapping, got list"):
        load_strategy(write_config(tmp_path, "- name: test_strategy\n"))


def test_a_repeated_top_level_key_is_refused(tmp_path: Path) -> None:
    # PyYAML's default is to keep the last silently. Here the second exit block
    # would replace the first, and the strategy that ran would not be the one
    # written down.
    text = BASE + "\nexit:\n  - {indicator: close, operator: above, target: 1}\n"
    with pytest.raises(StrategyConfigError, match="duplicate key 'exit'"):
        load_strategy(write_config(tmp_path, text))


# --------------------------------------------------------------------------
# The closed schema.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(BLOCKS))
def test_a_missing_required_block_names_it(tmp_path: Path, key: str) -> None:
    text = mutate(BLOCKS[key], "")
    with pytest.raises(StrategyConfigError, match=f"'{key}' is required and is missing"):
        load_strategy(write_config(tmp_path, text))


def test_an_unknown_top_level_key_is_refused(tmp_path: Path) -> None:
    # A typo here is the dangerous case: `entires:` beside a valid `entry:`
    # would load as a strategy whose entry rule nobody wrote.
    text = mutate("name: test_strategy\n", "name: test_strategy\nentires: []\n")
    with pytest.raises(StrategyConfigError, match=r"'<root>' carries unknown keys \['entires'\]"):
        load_strategy(write_config(tmp_path, text))


def test_an_unsupported_schema_version_is_refused(tmp_path: Path) -> None:
    text = mutate("version: 1\n", "version: 2\n")
    with pytest.raises(StrategyConfigError, match="'version' is 2, and this loader understands"):
        load_strategy(write_config(tmp_path, text))


def test_an_empty_description_is_refused(tmp_path: Path) -> None:
    text = mutate(BLOCKS["description"], 'description: "   "\n')
    with pytest.raises(StrategyConfigError, match="'description' must state the strategy"):
        load_strategy(write_config(tmp_path, text))


@pytest.mark.parametrize("name", ["../escape", "with space", "", "name/slash"])
def test_a_name_that_is_not_filename_safe_is_refused(tmp_path: Path, name: str) -> None:
    text = mutate("name: test_strategy\n", f'name: "{name}"\n')
    with pytest.raises(StrategyConfigError, match="'name' must be letters, digits"):
        load_strategy(write_config(tmp_path, text))


# --------------------------------------------------------------------------
# Conditions: flat, AND-combined, from a closed vocabulary.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["entry", "exit"])
def test_an_empty_condition_list_is_refused(tmp_path: Path, key: str) -> None:
    # A rule no condition constrains is true on every bar.
    text = mutate(BLOCKS[key], f"{key}: []\n")
    with pytest.raises(StrategyConfigError, match=f"'{key}' must hold at least one condition"):
        load_strategy(write_config(tmp_path, text))


def test_a_condition_list_that_is_not_a_list_is_refused(tmp_path: Path) -> None:
    text = mutate(BLOCKS["exit"], "exit: {indicator: close, operator: above, target: 1}\n")
    with pytest.raises(StrategyConfigError, match="'exit' must be a list, got dict"):
        load_strategy(write_config(tmp_path, text))


def test_an_expression_is_not_a_condition(tmp_path: Path) -> None:
    # The format has no expressions. A string here must fail loudly rather than
    # be parsed, compiled, or evaluated by anything.
    text = mutate(SECOND_ENTRY, '  - "close > sma(200) and volume > 1.5 * avg"\n')
    with pytest.raises(StrategyConfigError, match=r"'entry\[1\]' must be a mapping, got str"):
        load_strategy(write_config(tmp_path, text))


def test_a_conditional_key_inside_a_condition_is_refused(tmp_path: Path) -> None:
    # No `when`, no `if`, no `unless`. Nothing branches.
    text = mutate(
        SECOND_ENTRY,
        "  - {indicator: close, operator: above, target: 1, when: uptrend}\n",
    )
    with pytest.raises(StrategyConfigError, match=r"'entry\[1\]' carries unknown keys \['when'\]"):
        load_strategy(write_config(tmp_path, text))


def test_a_nested_target_is_refused(tmp_path: Path) -> None:
    # A target that is itself a condition is nesting, which this format does
    # not have. It is refused rather than half-read.
    text = mutate(
        SECOND_ENTRY,
        "  - {indicator: close, operator: above,"
        " target: {indicator: sma, period: 10, operator: above, target: 5}}\n",
    )
    with pytest.raises(
        StrategyConfigError,
        match=r"'entry\[1\].target' carries unknown keys \['operator', 'target'\]",
    ):
        load_strategy(write_config(tmp_path, text))


def test_an_unknown_indicator_is_refused(tmp_path: Path) -> None:
    text = mutate(SECOND_ENTRY, "  - {indicator: rsi, period: 14, operator: above, target: 30}\n")
    with pytest.raises(
        StrategyConfigError, match=r"'entry\[1\].indicator' is not in the indicator vocabulary"
    ):
        load_strategy(write_config(tmp_path, text))


def test_an_unknown_operator_is_refused(tmp_path: Path) -> None:
    # `below` is not in the vocabulary. Adding it is a code change with a test,
    # which is exactly what this asserts is required.
    text = mutate(SECOND_ENTRY, "  - {indicator: close, operator: below, target: 1}\n")
    with pytest.raises(
        StrategyConfigError, match=r"'entry\[1\].operator' is not in the operator vocabulary"
    ):
        load_strategy(write_config(tmp_path, text))


def test_the_vocabulary_the_message_offers_is_the_vocabulary_enforced() -> None:
    # The suggestion in a rejection message has to be usable. If these drift,
    # the operator is told to write a name the loader will also reject.
    assert set(INDICATORS) == {"close", "sma"}
    assert set(OPERATORS) == {"above", "crosses_above", "crosses_below"}


def test_an_indicator_that_needs_a_period_must_have_one(tmp_path: Path) -> None:
    text = mutate(SECOND_ENTRY, "  - {indicator: sma, operator: above, target: 1}\n")
    with pytest.raises(
        StrategyConfigError, match=r"'entry\[1\].period' is required by indicator 'sma'"
    ):
        load_strategy(write_config(tmp_path, text))


def test_an_indicator_that_takes_no_period_must_not_have_one(tmp_path: Path) -> None:
    text = mutate(SECOND_ENTRY, "  - {indicator: close, period: 20, operator: above, target: 1}\n")
    with pytest.raises(
        StrategyConfigError, match=r"'entry\[1\].period' is not accepted by indicator 'close'"
    ):
        load_strategy(write_config(tmp_path, text))


@pytest.mark.parametrize("period", [0, -5])
def test_a_period_below_one_bar_is_refused(tmp_path: Path, period: int) -> None:
    text = mutate("period: 200}", f"period: {period}}}")
    with pytest.raises(
        StrategyConfigError, match=r"'entry\[1\].target.period' must be at least 1 bar"
    ):
        load_strategy(write_config(tmp_path, text))


def test_a_period_that_is_neither_a_number_nor_a_placeholder_is_refused(tmp_path: Path) -> None:
    # A bare `fast` is a name the loader has no way to resolve. Only `$fast` is.
    text = mutate(
        '    period: "$fast"\n    operator: crosses_above\n',
        '    period: "fast"\n    operator: crosses_above\n',
    )
    with pytest.raises(
        StrategyConfigError, match=r"'entry\[0\].period' must be a whole number of bars"
    ):
        load_strategy(write_config(tmp_path, text))


def test_a_boolean_period_is_not_an_integer(tmp_path: Path) -> None:
    # YAML's `true` is an int to Python, so a bare isinstance check would read
    # it as a one-bar lookback.
    text = mutate("period: 200}", "period: true}")
    with pytest.raises(
        StrategyConfigError, match=r"'entry\[1\].target.period' must be an integer, got bool"
    ):
        load_strategy(write_config(tmp_path, text))


# --------------------------------------------------------------------------
# Parameters: every placeholder resolvable, every searched parameter used.
# --------------------------------------------------------------------------


def test_a_placeholder_with_no_parameter_behind_it_is_refused(tmp_path: Path) -> None:
    text = mutate(
        '    target: {indicator: sma, period: "$slow"}\n  - {indicator: close',
        '    target: {indicator: sma, period: "$medium"}\n  - {indicator: close',
    )
    with pytest.raises(
        StrategyConfigError,
        match=r"'entry\[0\].target.period' references \$medium, which is in neither",
    ):
        load_strategy(write_config(tmp_path, text))


def test_a_searched_parameter_nothing_references_is_refused(tmp_path: Path) -> None:
    # It would multiply the grid - and the number of chances to fit noise -
    # while changing no result, which is the worst way to lose a search.
    text = mutate(
        "  slow: {min: 40, max: 120, step: 20}\n",
        "  slow: {min: 40, max: 120, step: 20}\n  trend: {min: 100, max: 200, step: 50}\n",
    )
    with pytest.raises(
        StrategyConfigError, match=r"'search' declares parameters \['trend'\] that no condition"
    ):
        load_strategy(write_config(tmp_path, text))


def test_a_name_in_both_search_and_fixed_is_refused(tmp_path: Path) -> None:
    text = mutate("  position_size_pct: 95.0\n", "  position_size_pct: 95.0\n  fast: 10\n")
    with pytest.raises(
        StrategyConfigError, match=r"'fixed' repeats searched parameters \['fast'\]"
    ):
        load_strategy(write_config(tmp_path, text))


def test_a_placeholder_may_resolve_from_fixed(tmp_path: Path) -> None:
    # A period held constant on conventional grounds is legitimate. It just may
    # not also be searched.
    text = mutate(
        SECOND_ENTRY,
        '  - {indicator: close, operator: above, target: {indicator: sma, period: "$trend"}}\n',
    )
    text = text.replace("  position_size_pct: 95.0\n", "  position_size_pct: 95.0\n  trend: 200\n")
    definition = load_strategy(write_config(tmp_path, text))
    assert definition.fixed["trend"] == 200.0
    assert definition.entry[1].right == IndicatorRef(indicator="sma", period_param="trend")


@pytest.mark.parametrize(
    ("bound", "spec"),
    [
        ("min", "{min: 5.5, max: 30, step: 5}"),
        ("min", "{min: 0, max: 30, step: 5}"),
        ("min", "{min: -50, max: -10, step: 5}"),
        ("max", "{min: 5, max: 30.5, step: 5}"),
        ("step", "{min: 5, max: 30, step: 2.5}"),
        ("step", "{min: 5, max: 30, step: 0.3}"),
    ],
)
def test_a_searched_period_must_be_whole_bars_across_its_whole_range(
    tmp_path: Path, bound: str, spec: str
) -> None:
    # $fast is the left-hand period of an entry and an exit condition, so every
    # value this range produces is an SMA lookback. Half a bar does not exist and
    # neither does a lookback of minus ten. A fractional STEP matters as much as a
    # fractional min: the first cell is whole and every cell after it is not.
    text = mutate("fast: {min: 5, max: 30, step: 5}", f"fast: {spec}")
    with pytest.raises(
        StrategyConfigError,
        match=rf"'search\.fast\.{bound}' bounds the range for \$fast, .*"
        r"whole number of bars of at least 1",
    ):
        load_strategy(write_config(tmp_path, text))


def test_a_searched_range_of_whole_bars_is_accepted(tmp_path: Path) -> None:
    # The boundary on the accepting side: exactly 1 bar is a legal lookback, and
    # min == max is a single-cell range rather than an error.
    text = mutate("fast: {min: 5, max: 30, step: 5}", "fast: {min: 1, max: 1, step: 1}")
    assert load_strategy(write_config(tmp_path, text)).search["fast"] == {
        "min": 1.0,
        "max": 1.0,
        "step": 1.0,
    }


@pytest.mark.parametrize("value", ["200.5", "0", "-10"])
def test_a_fixed_period_that_is_not_a_whole_number_of_bars_is_refused(
    tmp_path: Path, value: str
) -> None:
    text = mutate(
        SECOND_ENTRY,
        '  - {indicator: close, operator: above, target: {indicator: sma, period: "$trend"}}\n',
    )
    text = text.replace(
        "  position_size_pct: 95.0\n", f"  position_size_pct: 95.0\n  trend: {value}\n"
    )
    with pytest.raises(
        StrategyConfigError, match="'fixed.trend' supplies a lookback period, so it must be"
    ):
        load_strategy(write_config(tmp_path, text))


# --------------------------------------------------------------------------
# The search block.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("step", ["0", "-5"])
def test_a_step_that_is_not_positive_is_refused(tmp_path: Path, step: str) -> None:
    text = mutate("fast: {min: 5, max: 30, step: 5}", f"fast: {{min: 5, max: 30, step: {step}}}")
    with pytest.raises(StrategyConfigError, match="'search.fast.step' must be greater than 0"):
        load_strategy(write_config(tmp_path, text))


def test_a_max_below_min_is_refused(tmp_path: Path) -> None:
    text = mutate("fast: {min: 5, max: 30, step: 5}", "fast: {min: 30, max: 5, step: 5}")
    with pytest.raises(StrategyConfigError, match=r"'search.fast.max' is below 'min' \(5.0 < 30.0"):
        load_strategy(write_config(tmp_path, text))


@pytest.mark.parametrize("bound", ["min", "max", "step"])
def test_a_range_missing_a_bound_is_refused(tmp_path: Path, bound: str) -> None:
    spec = {"min": "5", "max": "30", "step": "5"}
    del spec[bound]
    written = ", ".join(f"{key}: {value}" for key, value in spec.items())
    text = mutate("fast: {min: 5, max: 30, step: 5}", f"fast: {{{written}}}")
    with pytest.raises(
        StrategyConfigError, match=f"'search.fast.{bound}' is required and is missing"
    ):
        load_strategy(write_config(tmp_path, text))


def test_a_range_with_an_unknown_key_is_refused(tmp_path: Path) -> None:
    text = mutate(
        "fast: {min: 5, max: 30, step: 5}", "fast: {min: 5, max: 30, step: 5, scale: log}"
    )
    with pytest.raises(
        StrategyConfigError, match=r"'search.fast' carries unknown keys \['scale'\]"
    ):
        load_strategy(write_config(tmp_path, text))


@pytest.mark.parametrize("value", [".inf", "-.inf", ".nan"])
def test_a_non_finite_bound_is_refused(tmp_path: Path, value: str) -> None:
    # `max: .inf` is a range that never ends. It has to fail here, before a
    # grid tries to walk it.
    text = mutate("fast: {min: 5, max: 30, step: 5}", f"fast: {{min: 5, max: {value}, step: 5}}")
    with pytest.raises(StrategyConfigError, match="'search.fast.max' must be a finite number"):
        load_strategy(write_config(tmp_path, text))


def test_a_non_numeric_bound_is_refused(tmp_path: Path) -> None:
    text = mutate("fast: {min: 5, max: 30, step: 5}", 'fast: {min: 5, max: "thirty", step: 5}')
    with pytest.raises(StrategyConfigError, match="'search.fast.max' must be a number, got str"):
        load_strategy(write_config(tmp_path, text))


def test_a_range_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    text = mutate("fast: {min: 5, max: 30, step: 5}", "fast: [5, 30, 5]")
    with pytest.raises(StrategyConfigError, match="'search.fast' must be a mapping, got list"):
        load_strategy(write_config(tmp_path, text))


def test_a_non_numeric_fixed_value_is_refused(tmp_path: Path) -> None:
    text = mutate("  position_size_pct: 95.0\n", '  position_size_pct: "most of it"\n')
    with pytest.raises(
        StrategyConfigError, match="'fixed.position_size_pct' must be a number, got str"
    ):
        load_strategy(write_config(tmp_path, text))


# --------------------------------------------------------------------------
# The type the loader builds into.
# --------------------------------------------------------------------------


def test_an_indicator_ref_cannot_be_resolved_and_unresolved_at_once() -> None:
    with pytest.raises(ValueError, match="both a period"):
        IndicatorRef(indicator="sma", period=50, period_param="fast")
