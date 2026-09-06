"""Tests for reading a candidate out of the search artifact that nominated it.

Every check here refuses BEFORE the held-back window is read, which is the only
reason they are worth this much space: a mismatch discovered afterwards is
discovered after a permanent touch has been charged, and this is the one moment
in the project where "you can always run it again" is false.

No test here reads a real search artifact. `state/` is gitignored, so a
committed fixture would be the only artifact in the repository and would rot
against the schema the moment it was bumped. Each test builds the payload it
needs from a dictionary, which is also what makes it possible to omit exactly
one field at a time.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from strategylab.backtest.costs import CostModel
from strategylab.protocol import (
    Instruments,
    Protocol,
    Validation,
    Window,
    Windows,
)
from strategylab.search.loop import SEARCH_SCHEMA_VERSION
from strategylab.validate.candidate import CandidateError, read_candidate

# Synthetic, and deliberately not the real snapshot's hash. Nothing here needs a
# genuine digest - only that two of them differ - and a real one is a
# high-entropy string that the repository's secrets hook flags on every commit.
# Suppressing that hook with a pragma would train everyone to suppress it.
HASH = "b" * 64
OTHER_HASH = "a" * 64


def _protocol(version: int = 1) -> Protocol:
    """A protocol whose only interesting field here is its version."""
    return Protocol(
        protocol_version=version,
        created=date(2026, 8, 21),
        instruments=Instruments(research="SPY", execution="VUAG.L"),
        windows=Windows(
            in_sample=Window(start=date(2005, 1, 1), end=date(2018, 12, 31)),
            walk_forward=Window(start=date(2019, 1, 1), end=date(2024, 12, 31)),
            fold_months=12,
        ),
        costs=CostModel(spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.0005),
        validation=Validation(
            primary_metric="sharpe",
            min_sharpe=1.0,
            min_trades_per_fold=10,
            min_trades_pooled=30,
            max_drawdown_ceiling_pct=25.0,
            pass_margin=0.2,
        ),
        walk_forward_touches=0,
    )


def _artifact(**overrides: Any) -> dict[str, Any]:
    """A search artifact carrying a winner, with fields overridable one at a time."""
    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "protocol_version": 1,
        "strategy": "ma_crossover",
        "config": "configs/ma_crossover.yaml",
        "data_content_hash": HASH,
        "noise_ceiling_note": "Not computed: this search was run without --trials.",
        "winner": {
            "params": {"fast": 10, "slow": 80, "trend": 150},
            "sharpe": 0.6399777383485417,
            "max_drawdown_pct": 19.400678867828024,
            "plateau_score": 0.5863837224642837,
            "neighbour_count": 27,
            "degenerate": False,
        },
    }
    payload.update(overrides)
    return payload


def _written(tmp_path: Path, payload: Any) -> Path:
    """Write a payload as a search artifact and return its path."""
    path = tmp_path / "search-20260822T213227Z.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The happy path.
# --------------------------------------------------------------------------


def test_a_matching_artifact_yields_its_winner(tmp_path: Path) -> None:
    candidate = read_candidate(_written(tmp_path, _artifact()), _protocol(), HASH)

    assert candidate.params == {"fast": 10, "slow": 80, "trend": 150}
    assert candidate.strategy == "ma_crossover"
    assert candidate.config_path == Path("configs/ma_crossover.yaml")
    assert candidate.data_content_hash == HASH
    assert candidate.degenerate is False
    assert candidate.search_sharpe == pytest.approx(0.6399777383485417)
    assert candidate.search_plateau_score == pytest.approx(0.5863837224642837)


def test_the_searchs_noise_caveat_travels_with_the_candidate(tmp_path: Path) -> None:
    # A candidate never benchmarked against noise must carry that sentence into
    # the verdict, not lose it at the boundary between the two commands.
    candidate = read_candidate(_written(tmp_path, _artifact()), _protocol(), HASH)

    assert candidate.noise_ceiling_note.startswith("Not computed")


def test_a_null_metric_becomes_a_nan_rather_than_an_error(tmp_path: Path) -> None:
    # `write_search_result` writes a non-finite metric as JSON null. A winner is
    # only ever nominated on a finite plateau score, so a null here is a figure
    # recorded beside the decision, not one it rested on.
    winner = dict(_artifact()["winner"], sharpe=None)

    candidate = read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)

    assert candidate.search_sharpe != candidate.search_sharpe, "expected NaN"


# --------------------------------------------------------------------------
# The three cross-checks, each of which saves a touch.
# --------------------------------------------------------------------------


def test_an_artifact_from_a_different_schema_is_refused(tmp_path: Path) -> None:
    path = _written(tmp_path, _artifact(schema_version=SEARCH_SCHEMA_VERSION - 1))

    with pytest.raises(CandidateError, match="schema version"):
        read_candidate(path, _protocol(), HASH)


def test_an_artifact_from_a_different_protocol_version_is_refused(tmp_path: Path) -> None:
    # The parameters would come from one rulebook and every threshold judging
    # them from another. The artifact carries version 1 by default; the
    # discrimination is the protocol being version 2.
    path = _written(tmp_path, _artifact())

    with pytest.raises(CandidateError, match="protocol version"):
        read_candidate(path, _protocol(version=2), HASH)


def test_an_artifact_from_a_different_snapshot_is_refused(tmp_path: Path) -> None:
    # Vendors revise history. A candidate chosen on prices no longer on disk is
    # not this snapshot's candidate, and no number in either run says so.
    path = _written(tmp_path, _artifact())

    with pytest.raises(CandidateError, match="snapshot"):
        read_candidate(path, _protocol(), OTHER_HASH)


# --------------------------------------------------------------------------
# Artifacts with nothing to validate.
# --------------------------------------------------------------------------


def test_a_search_that_nominated_nobody_is_refused(tmp_path: Path) -> None:
    path = _written(tmp_path, _artifact(winner=None))

    with pytest.raises(CandidateError, match="nominated no candidate"):
        read_candidate(path, _protocol(), HASH)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CandidateError, match="cannot read"):
        read_candidate(tmp_path / "absent.json", _protocol(), HASH)


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "search-broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(CandidateError, match="not valid JSON"):
        read_candidate(path, _protocol(), HASH)


def test_a_json_document_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CandidateError, match="not a JSON object"):
        read_candidate(_written(tmp_path, [1, 2, 3]), _protocol(), HASH)


def test_a_winner_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CandidateError, match="not an object"):
        read_candidate(_written(tmp_path, _artifact(winner="best")), _protocol(), HASH)


def test_an_artifact_naming_no_config_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CandidateError, match="names no config"):
        read_candidate(_written(tmp_path, _artifact(config="")), _protocol(), HASH)


# --------------------------------------------------------------------------
# Parameters. A period is a whole number of bars or it is not a period.
# --------------------------------------------------------------------------


def test_a_winner_with_no_parameters_is_refused(tmp_path: Path) -> None:
    winner = dict(_artifact()["winner"], params={})

    with pytest.raises(CandidateError, match="no 'params' mapping"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)


def test_a_fractional_period_is_refused(tmp_path: Path) -> None:
    # A lookback of 10.5 bars is not a lookback, and coercing it silently would
    # validate a strategy nobody searched.
    winner = dict(_artifact()["winner"], params={"fast": 10.5})

    with pytest.raises(CandidateError, match="not a whole number of bars"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)


def test_a_boolean_period_is_refused(tmp_path: Path) -> None:
    # `True == 1` in Python, so a bare int check lets this through as a
    # one-bar lookback.
    winner = dict(_artifact()["winner"], params={"fast": True})

    with pytest.raises(CandidateError, match="not a whole number of bars"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)


def test_a_period_written_as_a_whole_float_is_accepted(tmp_path: Path) -> None:
    # JSON has one number type; 10.0 round-trips from an int and is a lookback.
    winner = dict(_artifact()["winner"], params={"fast": 10.0})

    candidate = read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)

    assert candidate.params == {"fast": 10}
    assert isinstance(candidate.params["fast"], int)


def test_a_non_numeric_metric_is_refused(tmp_path: Path) -> None:
    winner = dict(_artifact()["winner"], sharpe="0.64")

    with pytest.raises(CandidateError, match="not a number"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)


@pytest.mark.parametrize("period", [0, -5])
def test_a_period_below_one_bar_is_refused(tmp_path: Path, period: int) -> None:
    # Whole numbers, so a check for integrality alone lets them through. They
    # would then fail inside required_history as a ValueError, which the CLI
    # reports as "validation could not run" rather than "nothing to validate" -
    # and this module's job is to refuse an uninterpretable candidate by name.
    winner = dict(_artifact()["winner"], params={"fast": period})

    with pytest.raises(CandidateError, match="at least one bar"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)


# --------------------------------------------------------------------------
# Nothing is defaulted. A default would be recorded in the verdict as though
# the search had stated it.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["strategy", "noise_ceiling_note"])
def test_a_missing_top_level_field_is_refused_rather_than_defaulted(
    tmp_path: Path, field: str
) -> None:
    artifact = _artifact()
    del artifact[field]

    with pytest.raises(CandidateError, match=f"carries no '{field}'"):
        read_candidate(_written(tmp_path, artifact), _protocol(), HASH)


def test_a_missing_degenerate_flag_is_refused_rather_than_read_as_false(
    tmp_path: Path,
) -> None:
    # The unsafe direction: defaulting to False reads as "the search did not
    # flag this plateau", and the CLI's degeneracy warning then never prints.
    winner = dict(_artifact()["winner"])
    del winner["degenerate"]

    with pytest.raises(CandidateError, match="carries no 'degenerate'"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)


def test_a_missing_noise_note_cannot_silently_become_an_empty_caveat(
    tmp_path: Path,
) -> None:
    # Defaulting this to "" is exactly the sentence being dropped at the
    # boundary that this module's docstring promises it carries across.
    artifact = _artifact()
    del artifact["noise_ceiling_note"]

    with pytest.raises(CandidateError):
        read_candidate(_written(tmp_path, artifact), _protocol(), HASH)


@pytest.mark.parametrize(
    "absolute",
    [
        # BOTH FLAVOURS, because `Path` is the host's and each rejects only its
        # own. On Windows a POSIX absolute path has a root but no drive and
        # `is_absolute()` is False; on POSIX a Windows one has neither. An
        # artifact written on one platform and validated on the other is
        # exactly the case that carries a username through a one-flavour guard.
        #
        # None of these is home-directory-shaped, and that is not squeamishness:
        # this repository's own hygiene gate refuses a `/home/<name>` or
        # `Users` path in any tracked file, which is the same rule the guard
        # under test enforces at run time. A test for it should not violate it.
        "/srv/research/configs/ma_crossover.yaml",
        "D:\\research\\configs\\ma_crossover.yaml",
        "//fileserver/share/configs/ma_crossover.yaml",
    ],
)
def test_an_absolute_config_path_is_refused(tmp_path: Path, absolute: str) -> None:
    # A verdict records this path verbatim, and an absolute path on most
    # machines carries a username into a file this repository may publish.
    with pytest.raises(CandidateError, match="absolute path"):
        read_candidate(_written(tmp_path, _artifact(config=absolute)), _protocol(), HASH)


def test_a_drive_relative_windows_path_is_refused(tmp_path: Path) -> None:
    # "C:configs/x.yaml" has a drive and no root, so PureWindowsPath.is_absolute
    # says False while ntpath.isabs says True. It resolves against that drive's
    # current directory, which is not this repository.
    with pytest.raises(CandidateError, match="absolute path"):
        read_candidate(
            _written(tmp_path, _artifact(config="C:configs/ma_crossover.yaml")),
            _protocol(),
            HASH,
        )


@pytest.mark.parametrize(
    "traversal",
    [
        "../../configs/ma_crossover.yaml",
        "configs/../../../etc/ma_crossover.yaml",
    ],
)
def test_a_config_path_that_climbs_out_of_the_repository_is_refused(
    tmp_path: Path, traversal: str
) -> None:
    # "Not absolute" is weaker than "repository-relative", which is what the
    # returned field is documented as. A `..` path is relative under both
    # flavours, passes every absoluteness check, and can still name anything on
    # the machine - including a home directory, verbatim, in the verdict.
    with pytest.raises(CandidateError, match="climbs out of the repository"):
        read_candidate(_written(tmp_path, _artifact(config=traversal)), _protocol(), HASH)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy", ["ma_crossover"]),
        ("strategy", 7),
        ("noise_ceiling_note", 123),
        ("noise_ceiling_note", {"note": "x"}),
    ],
)
def test_a_non_string_text_field_is_refused_rather_than_stringified(
    tmp_path: Path, field: str, value: object
) -> None:
    # `str(["ma_crossover"])` is "['ma_crossover']" and would be recorded as the
    # strategy's name; `str(123)` would be written as the search's own sentence
    # and printed as the verdict's closing line. protocol.py already took this
    # project's position on that coercion for exactly this reason.
    with pytest.raises(CandidateError, match="is not a string"):
        read_candidate(_written(tmp_path, _artifact(**{field: value})), _protocol(), HASH)


@pytest.mark.parametrize("field", ["strategy", "noise_ceiling_note"])
def test_a_null_top_level_field_is_refused_rather_than_stringified(
    tmp_path: Path, field: str
) -> None:
    # Refusing absence alone moved the fault rather than removing it:
    # `str(None)` is the literal "None", which would be recorded in the verdict
    # as the strategy's name, or printed as the closing noise caveat.
    with pytest.raises(CandidateError, match=f"carries a null '{field}'"):
        read_candidate(_written(tmp_path, _artifact(**{field: None})), _protocol(), HASH)


def test_a_null_degenerate_flag_is_refused_rather_than_read_as_false(
    tmp_path: Path,
) -> None:
    # `bool(None)` is False - the same unsafe default in a different shape.
    winner = dict(_artifact()["winner"], degenerate=None)

    with pytest.raises(CandidateError, match="carries a null 'degenerate'"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)


@pytest.mark.parametrize("flag", ["false", 0, "yes"])
def test_a_degenerate_flag_that_is_not_a_boolean_is_refused(tmp_path: Path, flag: object) -> None:
    # `bool("false")` is True. Coercing would decide the plateau flag by
    # truthiness rather than by what the search found.
    winner = dict(_artifact()["winner"], degenerate=flag)

    with pytest.raises(CandidateError, match="not a boolean"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)


@pytest.mark.parametrize("metric", ["sharpe", "plateau_score"])
def test_a_metric_key_missing_entirely_is_refused_though_a_null_is_not(
    tmp_path: Path, metric: str
) -> None:
    # The two cases must not read alike. A null is a figure the search recorded
    # as non-finite and comes back as NaN; an absent key is a figure it never
    # recorded, and both would land in the verdict as `null`.
    winner = dict(_artifact()["winner"])
    del winner[metric]

    with pytest.raises(CandidateError, match=f"carries no '{metric}'"):
        read_candidate(_written(tmp_path, _artifact(winner=winner)), _protocol(), HASH)

    nulled = dict(_artifact()["winner"], **{metric: None})
    candidate = read_candidate(_written(tmp_path, _artifact(winner=nulled)), _protocol(), HASH)
    value = candidate.search_sharpe if metric == "sharpe" else candidate.search_plateau_score
    assert value != value, "a null metric is NaN, not a refusal"
