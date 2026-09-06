"""Tests for the strategy-config loader.

Two kinds of test live here. The first kind pins the SHAPE the loader produces
from a file, including which periods stay unresolved placeholders. The second,
and the larger, pins what it REFUSES: the declarative rule is only worth
something if a nested target, an expression, an unknown operator, or a searched
parameter nothing references stops the run rather than passing quietly into a
search that takes hours and answers a question nobody asked.

Every rejection test asserts on WHERE the message says the fault is - the dotted
field path for a fault in a field, the filename for a fault that is the file's
own - because an error that does not say where is an error the operator has to
hunt for.
"""

from __future__ import annotations

import re
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
    # Equality alone cannot see this: 200 == 200.0 and dataclass equality is
    # equality of fields, so a loader that stored a float would pass the line
    # above. A period is an index offset into a frame of bars, and a float one
    # is a bug that surfaces much later.
    assert isinstance(right, IndicatorRef)
    assert type(right.period) is int


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
    # StrategyDef.search is typed dict[str, dict[str, float]], and 5 == 5.0, so
    # the equality above holds whether the loader coerced or not. Pin the type.
    assert all(
        type(bound) is float for spec in definition.search.values() for bound in spec.values()
    )
    assert type(definition.fixed["position_size_pct"]) is float


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
    # A literal 1, not SUPPORTED_VERSION. The loader refuses any file whose
    # version differs from that constant, so comparing the loaded value back
    # against it cannot fail whatever either one is.
    assert definition.version == 1
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


def test_the_supported_schema_version_is_one() -> None:
    # Pinned here rather than inside a load, so that bumping the constant is a
    # deliberate act with a test to change rather than a silent widening.
    assert SUPPORTED_VERSION == 1


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
    # Both halves: that it says what went wrong, and that it says which file.
    # The name of this test claims the second, so it has to assert it.
    with pytest.raises(StrategyConfigError, match=r"absent\.yaml: file not found at "):
        load_strategy(tmp_path / "absent.yaml")


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StrategyConfigError, match="is empty"):
        load_strategy(write_config(tmp_path, ""))


def test_malformed_yaml_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StrategyConfigError, match="not valid YAML"):
        load_strategy(write_config(tmp_path, "entry: [\n"))


def test_a_root_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    # The top level is the file, not a field in it. Naming it `field '<root>'`
    # sent the operator looking for a line that does not exist.
    with pytest.raises(
        StrategyConfigError, match="must hold a mapping at the top level, got list"
    ) as caught:
        load_strategy(write_config(tmp_path, "- name: test_strategy\n"))
    assert "<root>" not in str(caught.value)


def test_deeply_nested_yaml_is_refused_rather_than_crashing(tmp_path: Path) -> None:
    # PyYAML's composer recurses once per level, so this raises RecursionError
    # from inside yaml.load. RecursionError is a RuntimeError, not a YAMLError,
    # so it escaped the loader's "any failure" contract until it was caught.
    text = mutate(BLOCKS["entry"], "entry: " + "[" * 4000 + "]" * 4000 + "\n")
    with pytest.raises(StrategyConfigError, match="nests too deeply to parse"):
        load_strategy(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("label", "field", "value"),
    [
        # float() of an integer past ~1.8e308 raises OverflowError, which is not
        # a ValueError and was escaping the loader's contract entirely.
        ("overflow in a search bound", "search", "9" * 400),
        ("overflow in a numeric target", "target", "9" * 400),
        # PyYAML CONSTRUCTS some scalars, and a construction that fails raises
        # out of the library rather than as a YAMLError. This one reaches
        # datetime and comes back as ValueError("month must be in 1..12").
        ("an impossible date", "fixed", "2021-19-45"),
        # CPython refuses to parse an integer literal longer than 4300 digits.
        ("an integer past the digit limit", "target", "1" * 4400),
    ],
)
def test_a_scalar_the_parser_chokes_on_is_refused(
    tmp_path: Path, label: str, field: str, value: str
) -> None:
    # Each of these is a malformed FILE, so each must come back as a
    # StrategyConfigError rather than as whatever the parser happened to raise.
    if field == "search":
        text = mutate(
            "fast: {min: 5, max: 30, step: 5}", f"fast: {{min: 5, max: {value}, step: 5}}"
        )
    elif field == "target":
        text = mutate(SECOND_ENTRY, f"  - {{indicator: close, operator: above, target: {value}}}\n")
    else:
        text = mutate("  position_size_pct: 95.0\n", f"  position_size_pct: 95.0\n  d: {value}\n")
    with pytest.raises(StrategyConfigError):
        load_strategy(write_config(tmp_path, text))


def test_a_path_with_an_embedded_null_byte_is_refused(tmp_path: Path) -> None:
    # os.open raises ValueError for this, which is not an OSError and so is not
    # covered by the read-path handlers around it.
    with pytest.raises(StrategyConfigError, match="cannot read"):
        load_strategy(tmp_path / "bad\0name.yaml")


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
    with pytest.raises(
        StrategyConfigError, match=r"carries unknown top-level keys \['entires'\]"
    ) as caught:
        load_strategy(write_config(tmp_path, text))
    assert "<root>" not in str(caught.value)


def test_an_unsupported_schema_version_is_refused(tmp_path: Path) -> None:
    text = mutate("version: 1\n", "version: 2\n")
    with pytest.raises(StrategyConfigError, match="'version' is 2, and this loader understands"):
        load_strategy(write_config(tmp_path, text))


def test_an_empty_description_is_refused(tmp_path: Path) -> None:
    text = mutate(BLOCKS["description"], 'description: "   "\n')
    with pytest.raises(StrategyConfigError, match="'description' must state the strategy"):
        load_strategy(write_config(tmp_path, text))


@pytest.mark.parametrize("name", ["../escape", "with space", "", "name/slash", "trailing\\n"])
def test_a_name_that_is_not_filename_safe_is_refused(tmp_path: Path, name: str) -> None:
    # The last case is the one a regex gets wrong for free: Python's `$` matches
    # before a single trailing newline, so `^[A-Za-z0-9_-]+$` accepts
    # "trailing\n" and a newline reaches a filename. The anchors are \A and \Z.
    text = mutate("name: test_strategy\n", f'name: "{name}"\n')
    with pytest.raises(StrategyConfigError, match="'name' must be letters, digits"):
        load_strategy(write_config(tmp_path, text))


def test_a_block_scalar_name_does_not_smuggle_in_a_newline(tmp_path: Path) -> None:
    # The same defect by the route a person would actually hit it: a YAML block
    # scalar appends "\n" without anyone writing one.
    text = mutate("name: test_strategy\n", "name: |\n  test_strategy\n")
    with pytest.raises(StrategyConfigError, match="'name' must be letters, digits"):
        load_strategy(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("indicator", "expected"),
    [
        ("close", r"'entry\[1\].period' is not accepted by indicator 'close'"),
        ("sma", r"'entry\[1\].period' is empty; it must be a count of bars"),
    ],
)
def test_an_explicitly_null_period_is_not_treated_as_absent(
    tmp_path: Path, indicator: str, expected: str
) -> None:
    # `period: null` is a period written down. On `close` it must be refused as
    # present-and-unwanted, not accepted by a value-is-None check; on `sma` the
    # operator must not be told it is "missing" while it sits on the line in
    # front of them.
    text = mutate(
        SECOND_ENTRY,
        f"  - {{indicator: {indicator}, period: null, operator: above, target: 1}}\n",
    )
    with pytest.raises(StrategyConfigError, match=expected):
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


def test_the_vocabulary_the_message_offers_is_the_vocabulary_enforced(tmp_path: Path) -> None:
    # The suggestion in a rejection message has to be usable. If these drift,
    # the operator is told to write a name the loader will also reject.
    assert set(INDICATORS) == {"close", "sma"}
    assert set(OPERATORS) == {"above", "crosses_above", "crosses_below"}

    # And the message must actually carry the list. Asserting the constants
    # alone leaves a loader free to enforce these and offer something else.
    for bad, good in (
        ("  - {indicator: rsi, period: 14, operator: above, target: 30}\n", sorted(INDICATORS)),
        ("  - {indicator: close, operator: below, target: 1}\n", sorted(OPERATORS)),
    ):
        text = mutate(SECOND_ENTRY, bad)
        with pytest.raises(StrategyConfigError) as caught:
            load_strategy(write_config(tmp_path, text))
        for name in good:
            # Substring containment is too weak here: "above" is inside
            # "crosses_above", so a message listing only the crossing operators
            # would satisfy `"above" in message` without ever offering `above`.
            assert re.search(rf"'{re.escape(name)}'", str(caught.value)), (
                f"{name!r} is enforced but is not offered as its own name in: {caught.value}"
            )

    # Every name the message offers must itself load, which is the property the
    # operator relies on when they act on the suggestion.
    for operator in sorted(OPERATORS):
        text = mutate(
            SECOND_ENTRY,
            f"  - {{indicator: close, operator: {operator}, target: 1}}\n",
        )
        assert load_strategy(write_config(tmp_path, text)).entry[1].operator == operator


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
        # Exactly ONE bound violates the rule in each case. A spec where two
        # bounds are bad - {min: -50, max: -10} was the original third case -
        # only records which check the implementation happens to run first, and
        # would keep passing if that order were reversed.
        ("min", "{min: 5.5, max: 30, step: 5}"),
        ("min", "{min: 0, max: 30, step: 5}"),
        ("min", "{min: -50, max: 30, step: 5}"),
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
    # neither does a lookback of minus fifty.
    #
    # A fractional STEP is refused for the same reason, though not for the reason
    # first written here. `step: 2.5` from `min: 5` gives 5, 7.5, 10, 12.5 - every
    # OTHER cell is whole, not "every cell after the first is fractional", which
    # is only true of a step like 0.3. The rule is that SOME cell the range
    # generates is not a whole number of bars, which both steps satisfy.
    #
    # `max: 30.5` is a different case again: with min 5 and step 5 the cells are
    # 5..30 and every one is legal. It is refused because a bound off the grid is
    # a bound the operator did not mean - the declared range and the walked range
    # must be the same range, or the config says one thing and the search does
    # another.
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
        StrategyConfigError,
        match=rf"'fixed\.trend' supplies a lookback period, .*of at least 1, got {value}",
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


LITERAL_TARGETS = """
name: literal_targets
version: 1
description: "Test fixture. Mechanism: none, this file exists to be parsed."
entry:
  - {indicator: close, operator: above, target: 400.0000001}
  - {indicator: close, operator: above, target: SECOND}
exit:
  - {indicator: close, operator: above, target: 1000000000.0}
fixed:
  position_size_pct: 95.0
"""


def test_two_literals_that_render_alike_in_one_list_are_refused(tmp_path: Path) -> None:
    # A CONFIG-LEVEL PROPERTY, CAUGHT WHERE VALIDATION IS DECLARED COMPLETE.
    # A decision log keys a literal comparand by its rendering at six
    # significant figures, and that rendering is not injective over floats.
    #
    # `core.strategy` refuses the pair too, and that guard is the backstop for a
    # StrategyDef built in code. It is not sufficient on its own: it tests the
    # ENTRY list only on a bar where no exit condition holds, so a collision
    # there could sit undetected through every exiting bar of a run. The loader
    # has no such branch.
    assert f"{400.0000001:.6g}" == f"{400.0000009:.6g}" == "400"
    path = write_config(tmp_path, LITERAL_TARGETS.replace("SECOND", "400.0000009"))

    with pytest.raises(StrategyConfigError, match="renders identically at six significant"):
        load_strategy(path)


def test_the_same_literal_twice_in_one_list_is_accepted(tmp_path: Path) -> None:
    # Not a lost value: the key carries the number it already had. Refusing this
    # would reject a legitimate config, which is what a guard stricter than the
    # thing it guards does.
    path = write_config(tmp_path, LITERAL_TARGETS.replace("SECOND", "400.0000001"))

    assert len(load_strategy(path).entry) == 2


def test_colliding_literals_in_different_lists_are_accepted(tmp_path: Path) -> None:
    # The two lists are evaluated separately and reported separately, so their
    # thresholds never share a mapping. Refusing across lists would reject a
    # config from which nothing can be lost.
    text = LITERAL_TARGETS.replace("SECOND", "13.5").replace(
        "target: 1000000000.0", "target: 400.0000009"
    )

    assert load_strategy(write_config(tmp_path, text)).name == "literal_targets"
