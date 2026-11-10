"""Tests for the risk layer - the veto gate between an intent and the venue.

THE LAYER IS UNTOUCHABLE, and these tests are the other half of that claim. It
has its own module and its own config file so that nothing a search or a strategy
does can reach its limits; it has its own test file for the same reason, and
nothing here imports from the search, the engine, or the strategy loader.

EVERY EQUITY IS PARAMETERISED, and that is not decoration. At an equity of 100 a
cash exposure and its percentage of equity are numerically identical, so an
implementation that never divides by equity reproduces every figure - the same
trap `mid = 100.00` sets in a cost test, and CLAUDE.md names it for that reason.
Each such test runs at 100, 12,500, 250,000 and 1,000,000, and the arithmetic is
chosen to be exact in binary floating point at all four: the fractions used are
an eighth, a quarter, a half and a sixteenth of equity, whose quotients are
exact, so an assertion can be an equality rather than an approximation.

**No number in this file was copied from what the implementation printed.** The
percentages are hand-derived from the cash amounts beside them: a realised loss
of one eighth of start-of-day equity is 12.5%, and 12.5 is what the assertion
says.

WHAT THESE TESTS DO NOT CLAIM. Nothing about live trading. The gate's one
caller is `live/worker.py`, tested with literals in `tests/test_worker.py`;
`strategylab run` has never been invoked against the venue, and the backtester
deliberately does not consult the risk layer.
"""

from __future__ import annotations

import dataclasses
import math
import re
from datetime import date
from pathlib import Path

import pytest

from strategylab.core.types import Action, Intent
from strategylab.risk.limits import (
    DEFAULT_LIMITS_PATH,
    PortfolioState,
    RiskLimits,
    Verdict,
    check,
    daily_loss_reached,
    load_limits,
)

# Anchored to this file rather than to the process working directory, so the four
# tests that load the COMMITTED limits file do not fail spuriously - with a
# FileNotFoundError that depends on where pytest was invoked from - when it runs
# from somewhere else. An earlier version of this comment said "the one test" and
# "quietly vanish"; the count had grown, and a missing file here is loud.
REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_LIMITS = REPO_ROOT / "configs" / "risk_limits.yaml"
REPO_PROTOCOL = REPO_ROOT / "protocol.yaml"

# Four account sizes, one of which is the trap. See the module docstring.
EQUITIES = (100.0, 12_500.0, 250_000.0, 1_000_000.0)

# The default limits file's own fields, as raw YAML text, so a test can omit or
# corrupt exactly one of them and leave the rest well-formed.
_FIELDS: dict[str, str] = {
    "max_position_pct": "95.0",
    "max_daily_loss_pct": "3.0",
    "max_open_exposure_pct": "95.0",
    "kill_switch": "false",
}


def _write(tmp_path: Path, text: str) -> Path:
    """Write a limits file and return its path."""
    path = tmp_path / "risk_limits.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _file(tmp_path: Path, **overrides: str | None) -> Path:
    """Write a limits file, overriding or omitting single fields.

    An override of ``None`` omits the key entirely, which is how the
    missing-field cases are built without hand-writing four near-identical files.
    """
    fields = dict(_FIELDS)
    for key, value in overrides.items():
        if value is None:
            del fields[key]
        else:
            fields[key] = value
    return _write(tmp_path, "".join(f"{key}: {value}\n" for key, value in fields.items()))


def _limits(
    *,
    max_position_pct: float = 95.0,
    max_daily_loss_pct: float = 3.0,
    max_open_exposure_pct: float = 95.0,
    kill_switch: bool = False,
) -> RiskLimits:
    """Limits matching the committed file, with single fields overridable."""
    return RiskLimits(
        max_position_pct=max_position_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_open_exposure_pct=max_open_exposure_pct,
        kill_switch=kill_switch,
    )


def _state(
    equity: float,
    *,
    start_of_day_equity: float | None = None,
    open_exposure: float = 0.0,
    realised_pnl_today: float = 0.0,
    daily_loss_tripped: bool = False,
) -> PortfolioState:
    """An account state, flat and unchanged on the day unless told otherwise.

    Cash amounts are in account currency units. ``start_of_day_equity`` defaults
    to ``equity``, which is an account that has not moved today.
    """
    return PortfolioState(
        as_of=date(2026, 9, 11),
        equity=equity,
        start_of_day_equity=equity if start_of_day_equity is None else start_of_day_equity,
        open_exposure=open_exposure,
        realised_pnl_today=realised_pnl_today,
        daily_loss_tripped=daily_loss_tripped,
    )


def _enter(size_pct: float = 95.0) -> Intent:
    """An entry intent asking for ``size_pct`` percent of equity."""
    return Intent(
        action=Action.ENTER_LONG,
        size_pct=size_pct,
        conditions_met=("sma($fast) crosses_above sma($slow)",),
    )


def _exit_long(size_pct: float = 0.0) -> Intent:
    """An exit intent. It asks for no new exposure unless told to.

    The size is settable only so the malformed case can be tested: an
    ``EXIT_LONG`` carrying a size contradicts ``Intent``'s own contract, and the
    gate is documented to read the size rather than the action.
    """
    return Intent(
        action=Action.EXIT_LONG,
        size_pct=size_pct,
        conditions_met=("sma($fast) crosses_below sma($slow)",),
    )


def _hold(size_pct: float = 0.0) -> Intent:
    """A hold. The size is settable only so the malformed case can be tested."""
    return Intent(
        action=Action.HOLD,
        size_pct=size_pct,
        conditions_failed=("sma($fast) crosses_above sma($slow)",),
    )


# ---------------------------------------------------------------------------
# load_limits - the file
# ---------------------------------------------------------------------------


def test_loads_the_committed_limits_file() -> None:
    limits = load_limits(REPO_LIMITS)
    assert limits.max_position_pct == 95.0
    assert limits.max_daily_loss_pct == 3.0
    assert limits.max_open_exposure_pct == 95.0
    assert limits.kill_switch is False


def test_the_committed_kill_switch_is_off() -> None:
    # Stated as its own test because it is the one field in the repository whose
    # value a reader will want confirmed rather than inferred.
    assert load_limits(REPO_LIMITS).kill_switch is False


def test_percentages_are_not_converted_to_fractions(tmp_path: Path) -> None:
    # The single percent-to-fraction conversion in this project is load_protocol's
    # and applies to the rates under `costs:`. A limit here is compared against
    # Intent.size_pct, which is also a percentage, so 95.0 must stay 95.0 - a
    # hundred-fold error would leave every limit slack by a factor of a hundred
    # and nothing would crash.
    limits = load_limits(_file(tmp_path, max_position_pct="95.0", max_daily_loss_pct="3.0"))
    # A hundred-fold error would land on 0.95, which the `== 95.0` below already
    # excludes; asserting `!= 0.95` separately would add a sentence, not a test.
    # `test_round_trips_a_well_formed_file` strictly contains this test; it is kept
    # because the unit boundary is the hazard CLAUDE.md says has already bitten
    # once, and a test named after a hazard is worth its two lines.
    assert limits.max_position_pct == 95.0
    assert limits.max_daily_loss_pct == 3.0


def test_default_path_points_at_the_risk_layers_own_file() -> None:
    assert Path("configs/risk_limits.yaml") == DEFAULT_LIMITS_PATH
    assert REPO_LIMITS.exists()


def test_the_protocol_file_is_not_a_limits_file() -> None:
    # The isolation claim, made testable: pointing the risk loader at
    # protocol.yaml is refused rather than partially honoured. That file has seven
    # unknown keys AND all four required ones missing, so which message appears
    # pins the order of the two checks - which configs/risk_limits.yaml now states.
    with pytest.raises(ValueError, match="unknown keys"):
        load_limits(REPO_PROTOCOL)


def test_round_trips_a_well_formed_file(tmp_path: Path) -> None:
    limits = load_limits(_file(tmp_path))
    assert limits == RiskLimits(
        max_position_pct=95.0,
        max_daily_loss_pct=3.0,
        max_open_exposure_pct=95.0,
        kill_switch=False,
    )


def test_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no file at"):
        load_limits(tmp_path / "absent.yaml")


def test_the_missing_file_message_says_there_is_no_default(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no default set of limits"):
        load_limits(tmp_path / "absent.yaml")


@pytest.mark.parametrize("key", sorted(_FIELDS))
def test_every_field_is_required(tmp_path: Path, key: str) -> None:
    path = _file(tmp_path, **{key: None})
    with pytest.raises(ValueError, match=f"'{key}' is required and is missing"):
        load_limits(path)


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "".join(f"{k}: {v}\n" for k, v in _FIELDS.items()) + "max_trades: 4\n")
    with pytest.raises(ValueError, match=re.escape("unknown keys ['max_trades']")):
        load_limits(path)


def test_a_misspelled_key_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    # `kill_swich: true` beside a valid set of limits would otherwise load as a
    # full set with the switch off, which is the failure the closed schema exists
    # for.
    text = "".join(f"{k}: {v}\n" for k, v in _FIELDS.items()) + "kill_swich: true\n"
    with pytest.raises(ValueError, match=re.escape("unknown keys ['kill_swich']")):
        load_limits(_write(tmp_path, text))


def test_a_repeated_key_is_refused(tmp_path: Path) -> None:
    # PyYAML's default keeps the last of a repeated key silently, so a file that
    # plainly reads `kill_switch: true` could be disarmed two lines later.
    text = "".join(f"{k}: {v}\n" for k, v in _FIELDS.items())
    with pytest.raises(ValueError, match="could not be parsed|not valid YAML"):
        load_limits(
            _write(
                tmp_path,
                text.replace("kill_switch: false", "kill_switch: true") + "kill_switch: false\n",
            )
        )


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is empty"):
        load_limits(_write(tmp_path, ""))


def test_a_top_level_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must hold a mapping of limits"):
        load_limits(_write(tmp_path, "- max_position_pct: 95.0\n"))


def test_unparseable_yaml_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not valid YAML"):
        load_limits(_write(tmp_path, "max_position_pct: [95.0\n"))


def test_a_file_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "risk_limits.yaml"
    path.write_bytes(b"max_position_pct: 95.0\nkill_switch: \xff\xfe\n")
    with pytest.raises(ValueError, match="not valid UTF-8"):
        load_limits(path)


def test_a_yaml_merge_key_is_refused(tmp_path: Path) -> None:
    # Not by design - the duplicate-key loader constructs every key node before
    # delegating, and `<<:` is still a merge-tagged scalar at that point. Asserted
    # because it is the behaviour an operator meets, and because refusing is the
    # right answer anyway: a limit assembled from an anchor defined elsewhere is a
    # limit nobody can read off the line it is written on.
    text = (
        "defaults: &d\n"
        "  max_position_pct: 95.0\n"
        "  max_daily_loss_pct: 3.0\n"
        "<<: *d\n"
        "max_open_exposure_pct: 95.0\n"
        "kill_switch: false\n"
    )
    with pytest.raises(ValueError, match="not valid YAML|could not be parsed"):
        load_limits(_write(tmp_path, text))


@pytest.mark.parametrize("key", ["max_position_pct", "max_daily_loss_pct", "max_open_exposure_pct"])
@pytest.mark.parametrize(
    "bad", ["0", "0.0", "-0.001", "-1.0", "100.01", "100.5", "101", ".nan", ".inf", "-.inf"]
)
def test_a_percentage_outside_its_range_is_refused(tmp_path: Path, key: str, bad: str) -> None:
    with pytest.raises(ValueError, match=f"'{key}' is a percentage"):
        load_limits(_file(tmp_path, **{key: bad}))


@pytest.mark.parametrize("key", ["max_position_pct", "max_daily_loss_pct", "max_open_exposure_pct"])
@pytest.mark.parametrize("bad", ['"95.0"', "true", "false", "[95.0]", "{value: 95.0}"])
def test_a_percentage_that_is_not_a_number_is_refused(tmp_path: Path, key: str, bad: str) -> None:
    with pytest.raises(ValueError, match=f"'{key}' must be a number of percent"):
        load_limits(_file(tmp_path, **{key: bad}))


@pytest.mark.parametrize("key", ["max_position_pct", "max_daily_loss_pct", "max_open_exposure_pct"])
def test_a_percentage_of_exactly_one_hundred_is_accepted(tmp_path: Path, key: str) -> None:
    # The literal, not the module's own PERCENT_CEILING: an expected value taken
    # from the code under test moves whenever that code does.
    limits = load_limits(_file(tmp_path, **{key: "100"}))
    assert getattr(limits, key) == 100.0


@pytest.mark.parametrize("key", ["max_position_pct", "max_daily_loss_pct", "max_open_exposure_pct"])
def test_a_very_small_percentage_is_accepted_through_the_loader(tmp_path: Path, key: str) -> None:
    # The constructor's accepted side is pinned beside its refusals; the LOADER's
    # was not, and every value it was asserted to accept was 95.0, 3.0, "7" or
    # "100". So the mutant the constructor test kills - a guard refusing anything
    # below 1.0 - still passed if it lived here instead.
    assert getattr(load_limits(_file(tmp_path, **{key: "0.001"})), key) == 0.001


@pytest.mark.parametrize("key", ["max_position_pct", "max_daily_loss_pct", "max_open_exposure_pct"])
def test_an_integer_percentage_is_read_as_a_float(tmp_path: Path, key: str) -> None:
    limits = load_limits(_file(tmp_path, **{key: "7"}))
    assert getattr(limits, key) == 7.0
    assert isinstance(getattr(limits, key), float)


@pytest.mark.parametrize(("text", "expected"), [("true", True), ("false", False)])
def test_the_kill_switch_reads_both_booleans(tmp_path: Path, text: str, expected: bool) -> None:
    assert load_limits(_file(tmp_path, kill_switch=text)).kill_switch is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", True),
        ("no", False),
        ("on", True),
        ("off", False),
        ("True", True),
        ("False", False),
        ("OFF", False),
    ],
)
def test_yaml_11_boolean_spellings_are_accepted_and_mean_what_they_read(
    tmp_path: Path, text: str, expected: bool
) -> None:
    # PyYAML resolves YAML 1.1's boolean spellings, so `kill_switch: off` is a
    # genuine False rather than the string "off". Asserted rather than forbidden
    # because each spelling means, to a reader, exactly what it loads as - the
    # trap is a QUOTED word, which is a string, and that is refused below. The
    # single letters `y` and `n` are NOT booleans to PyYAML and so land there too.
    assert load_limits(_file(tmp_path, kill_switch=text)).kill_switch is expected


@pytest.mark.parametrize("bad", ['"false"', '"true"', "0", "1", "y", "n", "[]", "null"])
def test_a_kill_switch_that_is_not_a_boolean_is_refused(tmp_path: Path, bad: str) -> None:
    # Both directions cost money and one of them costs it quietly: a quoted
    # "false" is a non-empty string and so truthy, while 0 is falsy and would
    # DISARM a switch someone believed they had set.
    with pytest.raises(ValueError, match="'kill_switch' must be true or false"):
        load_limits(_file(tmp_path, kill_switch=bad))


# ---------------------------------------------------------------------------
# check - the gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_clean_account_allows_the_full_requested_size(equity: float) -> None:
    verdict = check(_enter(95.0), _state(equity), _limits())
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 95.0
    assert verdict.reason == "no limit bound"


@pytest.mark.parametrize("equity", EQUITIES)
def test_the_position_limit_reduces_rather_than_rejects(equity: float) -> None:
    verdict = check(_enter(95.0), _state(equity), _limits(max_position_pct=10.0))
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 10.0
    assert "max_position_pct" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_request_at_exactly_the_position_limit_is_not_reduced(equity: float) -> None:
    verdict = check(_enter(95.0), _state(equity), _limits(max_position_pct=95.0))
    assert verdict.limited_size_pct == 95.0
    assert verdict.reason == "no limit bound"


@pytest.mark.parametrize("equity", EQUITIES)
def test_exposure_headroom_reduces_the_size(equity: float) -> None:
    # Open exposure is half of equity, so 50% of it. The ceiling is 95%, leaving
    # 45 points of headroom against a request for 95. Hand-derived: 95 - 50 = 45.
    verdict = check(
        _enter(95.0),
        _state(equity, open_exposure=equity / 2),
        _limits(max_open_exposure_pct=95.0),
    )
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 45.0
    assert "max_open_exposure_pct" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_exhausted_exposure_rejects(equity: float) -> None:
    # Exposure is half of equity against a ceiling of 50%: headroom is exactly
    # zero, and zero headroom is a rejection rather than an allowance of nothing.
    verdict = check(
        _enter(95.0),
        _state(equity, open_exposure=equity / 2),
        _limits(max_open_exposure_pct=50.0),
    )
    assert verdict.verdict is Verdict.REJECT
    assert verdict.limited_size_pct == 0.0
    assert "max_open_exposure_pct" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_exposure_over_the_ceiling_rejects(equity: float) -> None:
    verdict = check(
        _enter(95.0),
        _state(equity, open_exposure=equity),
        _limits(max_open_exposure_pct=95.0),
    )
    assert verdict.verdict is Verdict.REJECT
    assert "fully used" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_headroom_equal_to_the_request_binds_nothing(equity: float) -> None:
    # Exposure is a quarter of equity, so 25%; the ceiling of 50% leaves 25
    # points, which is exactly what is asked for. Nothing was reduced, so
    # nothing is reported as binding.
    verdict = check(
        _enter(25.0),
        _state(equity, open_exposure=equity / 4),
        _limits(max_open_exposure_pct=50.0),
    )
    assert verdict.limited_size_pct == 25.0
    assert verdict.reason == "no limit bound"


@pytest.mark.parametrize("equity", EQUITIES)
def test_the_tighter_of_two_reductions_wins_and_is_the_one_reported(equity: float) -> None:
    # Position cap 50, exposure headroom 50 - 25 = 25. The exposure limit is
    # tighter, so it sets the size and it is the limit named.
    verdict = check(
        _enter(95.0),
        _state(equity, open_exposure=equity / 4),
        _limits(max_position_pct=50.0, max_open_exposure_pct=50.0),
    )
    assert verdict.limited_size_pct == 25.0
    assert "max_open_exposure_pct" in verdict.reason
    assert "max_position_pct" not in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_the_position_cap_is_reported_when_it_is_the_tighter_one(equity: float) -> None:
    # Position cap 20, exposure headroom 25. The position cap binds, and 20 is
    # below the headroom so the exposure limit changes nothing.
    verdict = check(
        _enter(95.0),
        _state(equity, open_exposure=equity / 4),
        _limits(max_position_pct=20.0, max_open_exposure_pct=50.0),
    )
    assert verdict.limited_size_pct == 20.0
    assert "max_position_pct" in verdict.reason
    assert "max_open_exposure_pct" not in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_realised_loss_at_the_ceiling_rejects(equity: float) -> None:
    # A realised loss of one eighth of start-of-day equity is 12.5%, and the
    # ceiling is 12.5. Reaching it is enough: the comparison is inclusive.
    verdict = check(
        _enter(95.0),
        _state(equity, realised_pnl_today=-equity / 8),
        _limits(max_daily_loss_pct=12.5),
    )
    assert verdict.verdict is Verdict.REJECT
    assert verdict.limited_size_pct == 0.0
    assert "max_daily_loss_pct" in verdict.reason
    assert "realised loss" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_realised_loss_below_the_ceiling_allows(equity: float) -> None:
    # One sixteenth is 6.25%, which is below a ceiling of 12.5.
    verdict = check(
        _enter(95.0),
        _state(equity, realised_pnl_today=-equity / 16),
        _limits(max_daily_loss_pct=12.5),
    )
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 95.0


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_marked_down_account_rejects_with_nothing_realised(equity: float) -> None:
    # Equity is seven eighths of the session open, so the account is carrying a
    # 12.5% loss it has not sold out of. Realised profit and loss is zero.
    verdict = check(
        _enter(95.0),
        _state(equity - equity / 8, start_of_day_equity=equity),
        _limits(max_daily_loss_pct=12.5),
    )
    assert verdict.verdict is Verdict.REJECT
    assert "below the session open" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_an_unrealised_gain_cannot_mask_a_realised_loss(equity: float) -> None:
    # Realised: down an eighth, so 12.5% banked. Marked: equity is UP an eighth
    # on the day, so the mark-to-market test alone would pass this. Each of the
    # two tests exists because it catches what the other misses.
    verdict = check(
        _enter(95.0),
        _state(
            equity + equity / 8,
            start_of_day_equity=equity,
            realised_pnl_today=-equity / 8,
        ),
        _limits(max_daily_loss_pct=12.5),
    )
    assert verdict.verdict is Verdict.REJECT
    assert "realised loss" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_realised_gain_does_not_trip_the_daily_loss_limit(equity: float) -> None:
    verdict = check(
        _enter(95.0),
        _state(equity + equity / 8, start_of_day_equity=equity, realised_pnl_today=equity / 8),
        _limits(max_daily_loss_pct=1.0),
    )
    assert verdict.verdict is Verdict.ALLOW


@pytest.mark.parametrize("equity", EQUITIES)
def test_the_daily_loss_limit_rejects_rather_than_reduces(equity: float) -> None:
    # A tiny request is refused outright, not scaled down: the limit is about
    # whether to trade at all today.
    verdict = check(
        _enter(0.5),
        _state(equity, realised_pnl_today=-equity / 8),
        _limits(max_daily_loss_pct=12.5),
    )
    assert verdict.verdict is Verdict.REJECT
    assert verdict.limited_size_pct == 0.0


# ---------------------------------------------------------------------------
# check - the kill switch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent", [_enter(95.0), _hold(), _exit_long()], ids=["enter", "hold", "exit"]
)
def test_the_kill_switch_rejects_every_action(intent: Intent) -> None:
    verdict = check(intent, _state(250_000.0), _limits(kill_switch=True))
    assert verdict.verdict is Verdict.REJECT
    assert verdict.limited_size_pct == 0.0
    assert "kill switch" in verdict.reason


def test_the_kill_switch_refuses_an_exit_and_says_so() -> None:
    # The consequence is deliberate and documented: the switch stops the wire, it
    # does not flatten the account. The reason string has to say that, because a
    # log reader finding a refused exit will otherwise read it as a bug.
    verdict = check(_exit_long(), _state(250_000.0), _limits(kill_switch=True))
    assert "an exit included" in verdict.reason


def test_the_kill_switch_beats_an_otherwise_perfect_account() -> None:
    verdict = check(
        _enter(1.0),
        _state(1_000_000.0, open_exposure=0.0, realised_pnl_today=500.0),
        _limits(kill_switch=True),
    )
    assert verdict.verdict is Verdict.REJECT


def test_the_kill_switch_does_not_excuse_a_meaningless_denominator() -> None:
    # The start-of-day guard comes first, because an account whose session-open
    # equity is zero is a programming error either way and answering around it
    # would be inventing a verdict.
    with pytest.raises(ValueError, match="start_of_day_equity"):
        check(_enter(95.0), _state(250_000.0, start_of_day_equity=0.0), _limits(kill_switch=True))


# ---------------------------------------------------------------------------
# check - what a request for nothing gets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_hold_is_allowed_at_zero(equity: float) -> None:
    verdict = check(_hold(), _state(equity), _limits())
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 0.0
    assert "no size requested" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_an_exit_is_allowed_at_zero(equity: float) -> None:
    verdict = check(_exit_long(), _state(equity), _limits())
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 0.0


@pytest.mark.parametrize("equity", EQUITIES)
def test_an_exit_survives_a_blown_daily_loss_limit(equity: float) -> None:
    # THE TEST THIS DESIGN EXISTS FOR. A daily-loss limit that blocked an exit
    # would forbid the one action that stops the loss it is measuring.
    verdict = check(
        _exit_long(),
        _state(
            equity - equity / 2,
            start_of_day_equity=equity,
            open_exposure=equity,
            realised_pnl_today=-equity / 2,
        ),
        _limits(max_daily_loss_pct=1.0, max_open_exposure_pct=1.0),
    )
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 0.0


@pytest.mark.parametrize("equity", EQUITIES)
def test_an_exit_survives_exhausted_exposure(equity: float) -> None:
    verdict = check(
        _exit_long(),
        _state(equity, open_exposure=equity),
        _limits(max_open_exposure_pct=50.0),
    )
    assert verdict.verdict is Verdict.ALLOW


@pytest.mark.parametrize(
    "intent",
    [_enter(0.0), _hold(0.0), _exit_long(0.0)],
    ids=["enter_long", "hold", "exit_long"],
)
@pytest.mark.parametrize("equity", EQUITIES)
def test_a_zero_size_intent_of_ANY_action_survives_a_blown_limit(
    equity: float, intent: Intent
) -> None:
    # THE OTHER HALF OF THE SIZE-KEYED CLAIM, and it was missing. The sized-exit
    # tests below rule out a gate that exempts on the ACTION alone, but not one
    # exempting on `action in (EXIT_LONG, HOLD) and size == 0` - a conjunction
    # that would pass every other assertion in this file, because nothing else
    # presents a NON-EXIT action at a size of zero to a limit that has been
    # reached. Only `enter_long` discriminates that. `hold` discriminates the
    # narrower `action is EXIT_LONG and size == 0`. An earlier version of this
    # comment named the narrower mutant and then said only `enter_long` caught it,
    # which was false: `hold` catches that one too.
    #
    # The `exit_long` parameter repeats the INPUTS of
    # `test_an_exit_survives_a_blown_daily_loss_limit` above exactly, and is not
    # redundant: the reason assertion below is the file's only check that a
    # permitted zero-size exit reports "no size requested" rather than an
    # action-based exemption, which docs/decisions/0010 requires of the log. A
    # previous version called it symmetry; an audit read the assertions and
    # disagreed. Three parameters are not three units of coverage, nor one.
    verdict = check(
        intent,
        _state(
            equity - equity / 2,
            start_of_day_equity=equity,
            open_exposure=equity,
            realised_pnl_today=-equity / 2,
        ),
        _limits(max_daily_loss_pct=1.0, max_open_exposure_pct=1.0),
    )
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 0.0
    assert "no size requested" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_hold_carrying_a_size_meets_every_limit(equity: float) -> None:
    # An Intent's contract says a HOLD carries zero, so this intent is malformed.
    # The gate keys off the SIZE rather than the action so that a malformed one
    # is limited rather than waved through on the strength of its action name.
    verdict = check(_hold(50.0), _state(equity), _limits(max_position_pct=10.0))
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 10.0


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_hold_carrying_a_size_can_be_rejected(equity: float) -> None:
    verdict = check(
        _hold(50.0),
        _state(equity, realised_pnl_today=-equity / 8),
        _limits(max_daily_loss_pct=12.5),
    )
    assert verdict.verdict is Verdict.REJECT


# ---------------------------------------------------------------------------
# check - states and sizes that are not numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1.0, -0.5])
def test_a_size_that_is_not_a_usable_percentage_is_rejected(bad: float) -> None:
    verdict = check(_enter(bad), _state(250_000.0), _limits())
    assert verdict.verdict is Verdict.REJECT
    assert verdict.limited_size_pct == 0.0
    assert "no limit can be applied" in verdict.reason


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_an_unusable_equity_rejects_an_entry(bad: float) -> None:
    # A rejection rather than an exception: a blown or unreadable account is a
    # state a live worker can genuinely observe, and refusing to trade is the
    # safe answer to it.
    verdict = check(_enter(95.0), _state(bad, start_of_day_equity=250_000.0), _limits())
    assert verdict.verdict is Verdict.REJECT
    assert "account equity" in verdict.reason


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf, -math.inf])
def test_an_unusable_open_exposure_rejects_an_entry(bad: float) -> None:
    verdict = check(_enter(95.0), _state(250_000.0, open_exposure=bad), _limits())
    assert verdict.verdict is Verdict.REJECT
    assert "open exposure" in verdict.reason


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_an_unusable_realised_pnl_rejects_an_entry(bad: float) -> None:
    verdict = check(_enter(95.0), _state(250_000.0, realised_pnl_today=bad), _limits())
    assert verdict.verdict is Verdict.REJECT
    assert "realised profit and loss" in verdict.reason


@pytest.mark.parametrize("bad", [0.0, -1.0, -250_000.0, math.nan, math.inf, -math.inf])
def test_an_unusable_start_of_day_equity_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="start_of_day_equity must be a positive, finite amount"):
        check(_enter(95.0), _state(250_000.0, start_of_day_equity=bad), _limits())


@pytest.mark.parametrize(
    "intent", [_enter(95.0), _hold(), _exit_long()], ids=["enter", "hold", "exit"]
)
def test_the_start_of_day_guard_applies_to_every_action(intent: Intent) -> None:
    with pytest.raises(ValueError, match="start_of_day_equity"):
        check(intent, _state(250_000.0, start_of_day_equity=-1.0), _limits())


# ---------------------------------------------------------------------------
# The invariants every verdict must satisfy
# ---------------------------------------------------------------------------

# A spread of accounts and limits, each paired with an intent, covering every
# branch above. Used for the properties that must hold across all of them.
_CASES: tuple[tuple[Intent, PortfolioState, RiskLimits], ...] = tuple(
    (intent, state, limits)
    for equity in EQUITIES
    for intent, state, limits in (
        (_enter(95.0), _state(equity), _limits()),
        (_enter(95.0), _state(equity), _limits(max_position_pct=10.0)),
        (_enter(95.0), _state(equity, open_exposure=equity / 2), _limits()),
        (_enter(95.0), _state(equity, open_exposure=equity), _limits()),
        (_enter(95.0), _state(equity, realised_pnl_today=-equity / 8), _limits()),
        (_enter(95.0), _state(equity - equity / 8, start_of_day_equity=equity), _limits()),
        (_enter(95.0), _state(equity), _limits(kill_switch=True)),
        (_enter(0.001), _state(equity), _limits()),
        (_hold(), _state(equity), _limits()),
        (_exit_long(), _state(equity, open_exposure=equity), _limits()),
        # Deliberately NOT here: an ENTER_LONG asking for nothing against a breached
        # account. It was added once, and an audit showed no invariant below can
        # fail on it under any gate, correct or broken - a zero request satisfies
        # every predicate whether allowed or refused. Its discriminating assertions
        # live in test_a_zero_size_intent_of_ANY_action_survives_a_blown_limit,
        # which asserts the verdict and the reason this matrix does not.
    )
)


@pytest.mark.parametrize(("intent", "state", "limits"), _CASES)
def test_a_verdict_never_permits_more_than_was_requested(
    intent: Intent, state: PortfolioState, limits: RiskLimits
) -> None:
    # The gate may reduce a size. It may never raise one. What this catches is a
    # gate that returns the binding LIMIT rather than the limited size - the rows
    # with a request of 0.0 or 0.001 make that immediate. It does NOT catch a sign
    # error in the headroom arithmetic, which an earlier version of this comment
    # claimed: a widened headroom is a larger argument to a min that the request
    # already clamps, so every case here passes regardless. The headroom's sign is
    # pinned by test_exposure_headroom_reduces_the_size (45.0, where a sign error
    # gives 95.0) and test_exhausted_exposure_rejects instead.
    verdict = check(intent, state, limits)
    assert verdict.limited_size_pct <= intent.size_pct


@pytest.mark.parametrize(("intent", "state", "limits"), _CASES)
def test_a_rejection_always_permits_zero(
    intent: Intent, state: PortfolioState, limits: RiskLimits
) -> None:
    verdict = check(intent, state, limits)
    if verdict.verdict is Verdict.REJECT:
        assert verdict.limited_size_pct == 0.0


@pytest.mark.parametrize(("intent", "state", "limits"), _CASES)
def test_an_allowance_of_zero_belongs_only_to_a_request_for_zero(
    intent: Intent, state: PortfolioState, limits: RiskLimits
) -> None:
    # An ALLOW at zero would send a zero-size order to a venue. The only intents
    # entitled to one are the ones that asked for nothing.
    verdict = check(intent, state, limits)
    if verdict.verdict is Verdict.ALLOW and verdict.limited_size_pct == 0.0:
        assert intent.size_pct == 0.0


@pytest.mark.parametrize(("intent", "state", "limits"), _CASES)
def test_every_verdict_carries_a_reason(
    intent: Intent, state: PortfolioState, limits: RiskLimits
) -> None:
    # An allowed intent is logged with a reason too, so the decision log shows
    # that the gate ran rather than leaving it to be inferred from a silence.
    verdict = check(intent, state, limits)
    assert verdict.reason.strip()


@pytest.mark.parametrize(("intent", "state", "limits"), _CASES)
def test_a_verdict_is_repeatable(intent: Intent, state: PortfolioState, limits: RiskLimits) -> None:
    # Compares the function against itself, so it cannot catch a wrong number. It
    # catches hidden state, a wall-clock dependence, and a RiskVerdict that has
    # lost value equality - and it is not evidence about any figure.
    assert check(intent, state, limits) == check(intent, state, limits)


# ---------------------------------------------------------------------------
# The two denominators. Each of these separates the documented rule from a
# plausible wrong one. TWO of the three are the only witness to their rule; the
# realised-route case is not, and says so - the masking test above already
# discriminates that denominator, which a mutation run confirmed by going red
# there first. It is kept as a direct statement of the rule with a wider margin.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("equity", EQUITIES)
def test_the_marked_down_route_divides_by_the_session_open(equity: float) -> None:
    # Session open is nine eighths of current equity, so the account is down
    # 0.125/1.125 = 11.11% of what it opened at, which a ceiling of 12.0 permits.
    # Dividing the same mark-down by CURRENT equity gives exactly 12.5%, which
    # that ceiling refuses. Every other mark-down test here asserts a REJECT, and
    # a REJECT cannot tell the two apart: for a loss the wrong denominator is
    # always the smaller number and so always yields the larger percentage.
    verdict = check(
        _enter(95.0),
        _state(equity, start_of_day_equity=equity + equity / 8),
        _limits(max_daily_loss_pct=12.0),
    )
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 95.0


@pytest.mark.parametrize("equity", EQUITIES)
def test_the_realised_route_divides_by_the_session_open(equity: float) -> None:
    # The same rule for the realised route, stated directly. NOT the only witness
    # to it: `test_an_unrealised_gain_cannot_mask_a_realised_loss` above already
    # separates the two denominators (12.5% correct against 11.111% wrong, either
    # side of a 12.5 ceiling), and a mutation run goes red there first. This one
    # restates it with a wider margin.
    #
    # The account has to be marked UP for the arithmetic to discriminate, and the
    # operative reason is not that a marked-DOWN account would trip the other
    # route first - a 0.1% mark-down would not. It is that only equity above the
    # session open makes the wrong denominator the LARGER number, and so the wrong
    # percentage the smaller one: 12.5% of the session open against 6.25% of
    # current equity, either side of a ceiling of 10.0. The state is deliberately
    # extreme because what is under test is which denominator was used, not a
    # plausible day. Note that this case's own four-equity fan-out buys nothing -
    # it asserts a REJECT, which a never-divides implementation also produces. The
    # fan-out earns its keep in the two tests either side of it.
    verdict = check(
        _enter(95.0),
        _state(
            equity * 2,
            start_of_day_equity=equity,
            realised_pnl_today=-equity / 8,
        ),
        _limits(max_daily_loss_pct=10.0),
    )
    assert verdict.verdict is Verdict.REJECT
    assert "realised loss" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_the_exposure_limit_divides_by_current_equity(equity: float) -> None:
    # The book is seven eighths of the session open and the account has fallen to
    # seven eighths of it, so the position is 100% of what is left: no headroom,
    # refused. Measured against start-of-day equity it would read 87.5% and permit
    # a further 7.5 points. This is the denominator error that COSTS money rather
    # than erring safe - it hands out headroom exactly when a drawdown has made the
    # open position too large.
    #
    # The numbers are chosen so the daily-loss routes cannot answer first under
    # EITHER denominator: the mark-down is 12.5% of the session open, or 14.2857%
    # of current equity if that route is also wrong, and both sit under the 20.0
    # ceiling set here. An earlier version used a halved account and a ceiling of
    # 100.0, where a co-occurring mark-down bug rejects at exactly 100.0 - that
    # version would have gone RED, but on the reason assertion rather than on the
    # verdict, so it would have blamed the exposure limit for a mark-down bug. The
    # re-based numbers make the verdict itself unambiguous instead of leaving a
    # string comparison to sort out which limit answered.
    #
    # At E = 100 this is the ONLY equity that catches an implementation which
    # never divides IN THE EXPOSURE ROUTE: 87.5 raw is under the 95 ceiling and
    # allows, while at every larger equity the raw cash figure exceeds 95 and
    # rejects anyway, so every assertion passes on wrong arithmetic. (A gate that
    # never divides ANYWHERE is caught at all four equities, because the raw
    # mark-down then trips the 20.0 ceiling and the reason assertion fails.) That
    # inverts the module docstring's framing of E = 100 as the trap, and is a
    # reason never to trim it from this test.
    verdict = check(
        _enter(95.0),
        _state(
            equity - equity / 8,
            start_of_day_equity=equity,
            open_exposure=equity - equity / 8,
        ),
        _limits(max_daily_loss_pct=20.0, max_open_exposure_pct=95.0),
    )
    assert verdict.verdict is Verdict.REJECT
    assert verdict.limited_size_pct == 0.0
    assert "max_open_exposure_pct" in verdict.reason


# ---------------------------------------------------------------------------
# The shipped configuration, and the malformed exit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("realised", "expected"),
    [(-7_000.0, Verdict.ALLOW), (-8_000.0, Verdict.REJECT)],
    ids=["2.8pct", "3.2pct"],
)
def test_the_committed_daily_loss_ceiling_binds(realised: float, expected: Verdict) -> None:
    # Every other daily-loss test overrides the ceiling to keep the arithmetic
    # exact, which left the SHIPPED 3.0 asserted to load and never to bind.
    # 7000/250000 is 2.8% of start-of-day equity and 8000/250000 is 3.2%, so this
    # brackets the ceiling rather than sitting on it: 3% of an account is not an
    # exact binary fraction, and an equality on the boundary would be fragile
    # where this is not. Equity equals the session open here too, so the
    # mark-to-market route reads 0% and only the realised route can answer.
    verdict = check(
        _enter(95.0),
        _state(250_000.0, realised_pnl_today=realised),
        load_limits(REPO_LIMITS),
    )
    assert verdict.verdict is expected


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_sized_exit_is_gated_on_its_size(equity: float) -> None:
    # `Intent` says an EXIT_LONG carries zero and nothing enforces it. The
    # exemption belongs to the SIZE, so a malformed sized exit meets the daily
    # loss limit like any other request. The two exit tests above pass because
    # their size is zero, NOT because their action is an exit, and this is the
    # test that shows the difference - without it, the config file's sentence
    # would be read as a promise the gate does not make.
    # A quarter of equity realised against a 12.5 ceiling: twelve and a half points
    # clear of it, so a regression in the ceiling's INCLUSIVITY cannot turn this
    # red and be misread as a failure of the size-gating rule. Equity is left at
    # the session open, which implies an exactly offsetting unrealised gain - not
    # an account a venue would report, and deliberate: it holds the mark-to-market
    # route at 0% so the rejection can only have come from the realised one.
    verdict = check(
        _exit_long(50.0),
        _state(equity, realised_pnl_today=-equity / 4),
        _limits(max_daily_loss_pct=12.5),
    )
    assert verdict.verdict is Verdict.REJECT
    assert verdict.limited_size_pct == 0.0
    assert "realised loss" in verdict.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_sized_exit_is_capped_like_any_other_request(equity: float) -> None:
    verdict = check(_exit_long(95.0), _state(equity), _limits(max_position_pct=10.0))
    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 10.0


# ---------------------------------------------------------------------------
# The type's own invariants. A limit reaches `check` either from the file or
# from a constructor, and only one of those two used to be guarded.
# ---------------------------------------------------------------------------


def _limits_with(field: str, value: float) -> RiskLimits:
    """Limits with one of the three percentages replaced.

    Written field by field rather than by unpacking a mapping, because
    ``_limits(**{field: value})`` is not type-safe: ``kill_switch`` is a bool and
    a mapping of floats could reach it.
    """
    if field == "max_position_pct":
        return _limits(max_position_pct=value)
    if field == "max_daily_loss_pct":
        return _limits(max_daily_loss_pct=value)
    return _limits(max_open_exposure_pct=value)


@pytest.mark.parametrize(
    "field", ["max_position_pct", "max_daily_loss_pct", "max_open_exposure_pct"]
)
# 100.01 sits just outside the inclusive upper bound. Without it a guard admitting
# anything up to 100.4 passes the whole file, which a mutation run demonstrated.
@pytest.mark.parametrize(
    "bad", [0.0, -5.0, -0.001, 100.01, 100.5, 101.0, math.nan, math.inf, -math.inf]
)
def test_a_limit_outside_its_range_cannot_be_constructed(field: str, bad: float) -> None:
    with pytest.raises(ValueError, match=f"{field} is a percentage"):
        _limits_with(field, bad)


@pytest.mark.parametrize(
    "field", ["max_position_pct", "max_daily_loss_pct", "max_open_exposure_pct"]
)
@pytest.mark.parametrize("good", [0.001, 1.0, 50.0, 100.0])
def test_a_limit_inside_its_range_is_accepted(field: str, good: float) -> None:
    # The refusals above pin only one side. Without this, a guard refusing
    # everything below 1.0 would pass the suite - nothing else constructs a limit
    # smaller than that - and so would one admitting 100.2. The inclusive upper
    # bound is touched NOWHERE else at the constructor: no `_limits(...)` call in
    # this file passes 100.0, and the only other 100 is a string through the
    # loader. An earlier version of this comment said the bound was covered
    # incidentally by a ceiling inside a denominator test, which was true until the
    # same round re-based that test onto 20.0.
    assert getattr(_limits_with(field, good), field) == good


@pytest.mark.parametrize("bad", [-5.0, -math.inf])
def test_a_negative_limit_can_no_longer_reach_a_verdict(bad: float) -> None:
    # What this guards, measured before the guard existed: a hand-built
    # RiskLimits(max_position_pct=-5.0) against a healthy account returned ALLOW
    # at limited_size_pct=-5.0 - an approved order for minus five percent of
    # equity, which `logging/decisions.py` accepts as a finite number and writes
    # into an append-only log. -inf returned ALLOW at -inf, which that module
    # refuses at the point of WRITING rather than of deciding. The range check
    # lived only in the loader, so only a file was guarded.
    # Two messages now, one per reason, so the match is on what they share: a
    # negative finite limit is out of range and -inf is not finite, and telling an
    # operator holding one about the other's problem was its own audit finding.
    with pytest.raises(ValueError, match="is a percentage and must be"):
        RiskLimits(
            max_position_pct=bad,
            max_daily_loss_pct=3.0,
            max_open_exposure_pct=95.0,
            kill_switch=False,
        )


def test_a_nan_limit_cannot_be_constructed_because_it_would_never_bind() -> None:
    # `nan < 95.0` is False, so a nan limit does not reduce anything and does not
    # reject anything: it is a limit that silently ceases to exist, which is worse
    # than a wrong one because nothing in a log shows it was there.
    with pytest.raises(ValueError, match="never binds at all"):
        _limits(max_position_pct=math.nan)


def test_the_kill_switch_must_be_stated_when_limits_are_built_in_code() -> None:
    # It carried a default of False until an audit pointed out that the one
    # omission the type made cheap was the PERMISSIVE one, in the module whose
    # whole subject is refusing things.
    with pytest.raises(TypeError):
        RiskLimits(  # type: ignore[call-arg]
            max_position_pct=95.0,
            max_daily_loss_pct=3.0,
            max_open_exposure_pct=95.0,
        )


def test_the_committed_file_survives_the_types_own_range_check() -> None:
    # The loader validates, then constructs, so the shipped file passes through
    # both checks. Asserted so that a future widening of one is not mistaken for
    # a widening of both.
    limits = load_limits(REPO_LIMITS)
    assert (
        RiskLimits(
            max_position_pct=limits.max_position_pct,
            max_daily_loss_pct=limits.max_daily_loss_pct,
            max_open_exposure_pct=limits.max_open_exposure_pct,
            kill_switch=limits.kill_switch,
        )
        == limits
    )


def test_the_verdict_vocabulary_is_closed() -> None:
    # logging/decisions.py records `risk_verdict` as this enum's string value, so
    # these two strings are part of the decision log's schema.
    assert {member.value for member in Verdict} == {"allow", "reject"}


# --------------------------------------------------------------------------
# The latch: a limit reached earlier in the session stays reached.
# --------------------------------------------------------------------------
#
# EVERY FIGURE IS AN EIGHTH OR A SIXTEENTH of the session open, so the
# percentages are exact in binary and the same at every account size: an eighth
# is 12.5% and a sixteenth is 6.25%. Against a limit of 12.5%, an eighth is
# reached - the comparison is inclusive - and a sixteenth is not.


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_mark_down_of_exactly_the_limit_is_reached_and_half_of_it_is_not(equity: float) -> None:
    limits = _limits(max_daily_loss_pct=12.5)

    at_the_limit = _state(equity - equity / 8, start_of_day_equity=equity)
    half_way = _state(equity - equity / 16, start_of_day_equity=equity)

    assert daily_loss_reached(at_the_limit, limits) is True
    assert daily_loss_reached(half_way, limits) is False


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_realised_loss_of_exactly_the_limit_is_reached_and_half_of_it_is_not(
    equity: float,
) -> None:
    limits = _limits(max_daily_loss_pct=12.5)

    assert daily_loss_reached(_state(equity, realised_pnl_today=-equity / 8), limits) is True
    assert daily_loss_reached(_state(equity, realised_pnl_today=-equity / 16), limits) is False


@pytest.mark.parametrize("equity", EQUITIES)
def test_an_account_that_is_ahead_has_reached_nothing(equity: float) -> None:
    ahead = _state(equity + equity / 8, start_of_day_equity=equity, realised_pnl_today=equity / 8)

    assert daily_loss_reached(ahead, _limits(max_daily_loss_pct=12.5)) is False


@pytest.mark.parametrize("equity", EQUITIES)
def test_whether_the_limit_is_reached_now_ignores_whether_it_was_before(equity: float) -> None:
    # It reports where the account stands. Remembering is the caller's job, and
    # an answer that echoed the latch back could never be used to set it.
    recovered = _state(equity, daily_loss_tripped=True)

    assert daily_loss_reached(recovered, _limits(max_daily_loss_pct=12.5)) is False


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_tripped_limit_refuses_a_sized_intent_on_an_account_that_has_recovered(
    equity: float,
) -> None:
    limits = _limits(max_daily_loss_pct=12.5)
    # Back at the session open with nothing realised: neither route is anywhere
    # near the limit now.
    untripped = check(_enter(95.0), _state(equity), limits)
    tripped = check(_enter(95.0), _state(equity, daily_loss_tripped=True), limits)

    assert untripped.verdict is Verdict.ALLOW, "the control: without the latch this is allowed"
    assert tripped.verdict is Verdict.REJECT
    assert tripped.limited_size_pct == 0.0
    assert "earlier in this session" in tripped.reason


@pytest.mark.parametrize("equity", EQUITIES)
def test_a_tripped_limit_still_lets_an_exit_through(equity: float) -> None:
    # The latch is a limit on NEW exposure. Blocking the exit would forbid the
    # one action that stops the loss the limit is measuring.
    verdict = check(_exit_long(), _state(equity, daily_loss_tripped=True), _limits())

    assert verdict.verdict is Verdict.ALLOW
    assert verdict.limited_size_pct == 0.0


def test_the_kill_switch_is_named_before_a_tripped_limit() -> None:
    verdict = check(
        _enter(95.0), _state(10_000.0, daily_loss_tripped=True), _limits(kill_switch=True)
    )

    assert verdict.verdict is Verdict.REJECT
    assert "kill switch" in verdict.reason


def test_a_state_is_untripped_unless_somebody_says_otherwise() -> None:
    assert _state(10_000.0).daily_loss_tripped is False


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_asking_whether_the_limit_is_reached_needs_a_session_open_to_divide_by(
    bad: float,
) -> None:
    with pytest.raises(ValueError, match="start_of_day_equity"):
        daily_loss_reached(_state(10_000.0, start_of_day_equity=bad), _limits())


@pytest.mark.parametrize("field", ["equity", "realised_pnl_today"])
def test_a_loss_that_cannot_be_read_has_not_been_shown_to_be_reached(field: str) -> None:
    state = _state(10_000.0)
    unreadable = dataclasses.replace(state, **{field: math.nan})

    assert daily_loss_reached(unreadable, _limits()) is False
