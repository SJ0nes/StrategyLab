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
from strategylab.core.config import config_content_hash
from strategylab.protocol import (
    Instruments,
    Protocol,
    Validation,
    Window,
    Windows,
)
from strategylab.search.loop import SEARCH_SCHEMA_VERSION
from strategylab.search.noise import NoiseGate
from strategylab.validate.candidate import CandidateError, read_candidate

# Synthetic, and deliberately not the real snapshot's hash. Nothing here needs a
# genuine digest - only that two of them differ - and a real one is a
# high-entropy string that the repository's secrets hook flags on every commit.
# Suppressing that hook with a pragma would train everyone to suppress it.
HASH = "b" * 64
OTHER_HASH = "a" * 64

#: The config the default artifact names. It is the committed one, read from the
#: repository root, which is where every test in this project runs from.
COMMITTED_CONFIG = "configs/ma_crossover.yaml"


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


def _gate(**overrides: Any) -> dict[str, Any]:
    """A noise-gate object for a candidate that passed, overridable one field at a time.

    The winner's Sharpe of 0.64 beat 97 of 100 surrogates, where 95 were needed.
    """
    gate: dict[str, Any] = {
        "status": "passed",
        "percentile": 95,
        "min_trials": 100,
        "candidate_sharpe": 0.6399777383485417,
        "scoreable_trials": 100,
        "beaten": 97,
        "required": 95,
        "summary": "The candidate's Sharpe of 0.640 beat 97 of 100 surrogates.",
    }
    gate.update(overrides)
    return gate


#: The gate a search records beside a winner whose Sharpe is not a number: it
#: beat none of the hundred surrogates, so it failed.
NO_SHARPE_GATE = _gate(status="failed", candidate_sharpe=None, beaten=0)


def _artifact(**overrides: Any) -> dict[str, Any]:
    """A search artifact carrying a winner, with fields overridable one at a time."""
    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "protocol_version": 1,
        "strategy": "ma_crossover",
        "config": COMMITTED_CONFIG,
        "data_content_hash": HASH,
        "noise_ceiling_note": "100 trials supports reading the upper percentiles.",
        "noise_gate": _gate(),
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
    if "config_content_hash" not in overrides:
        # Computed, not pasted: a literal digest would be a high-entropy string
        # in a tracked file, and it would go stale the first time the committed
        # config changed for a good reason. Computed only when the caller did
        # not supply one, because a test that stands in its own repository root
        # has no committed config under it to hash.
        payload["config_content_hash"] = config_content_hash(Path(COMMITTED_CONFIG))
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
    assert candidate.config_path == Path(COMMITTED_CONFIG)
    assert candidate.config_content_hash == config_content_hash(Path(COMMITTED_CONFIG))
    assert candidate.data_content_hash == HASH
    assert candidate.degenerate is False
    assert candidate.search_sharpe == pytest.approx(0.6399777383485417)
    assert candidate.search_plateau_score == pytest.approx(0.5863837224642837)


def test_the_searchs_noise_caveat_travels_with_the_candidate(tmp_path: Path) -> None:
    # Verbatim, whatever it says. The search wrote one sentence about what its
    # ceiling supports, and it must reach the verdict rather than be dropped or
    # paraphrased at the boundary between the two commands.
    note = "Not computed: this search was run without --trials."
    unbenchmarked = _gate(status="not_computed", scoreable_trials=0, beaten=None, required=None)
    artifact = _artifact(noise_ceiling_note=note, noise_gate=unbenchmarked)

    candidate = read_candidate(
        _written(tmp_path, artifact), _protocol(), HASH, require_noise_gate=False
    )

    assert candidate.noise_ceiling_note == note


def test_a_null_metric_becomes_a_nan_rather_than_an_error(tmp_path: Path) -> None:
    # `write_search_result` writes a non-finite metric as JSON null. A winner is
    # only ever nominated on a finite plateau score, so a null here is a figure
    # recorded beside the decision, not one it rested on.
    #
    # A Sharpe that is not a number beats no surrogate, so the gate beside it
    # reads `failed`, and only a caller that waives the gate can read it at all.
    winner = dict(_artifact()["winner"], sharpe=None)
    artifact = _artifact(winner=winner, noise_gate=NO_SHARPE_GATE)

    candidate = read_candidate(
        _written(tmp_path, artifact), _protocol(), HASH, require_noise_gate=False
    )

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


@pytest.mark.parametrize("field", ["strategy", "noise_ceiling_note", "config_content_hash"])
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
        ("config_content_hash", 7),
        ("config_content_hash", ["b" * 64]),
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


@pytest.mark.parametrize("field", ["strategy", "noise_ceiling_note", "config_content_hash"])
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
    # A null Sharpe cannot have passed the gate, so the gate beside it says so.
    gate = NO_SHARPE_GATE if metric == "sharpe" else _gate()
    candidate = read_candidate(
        _written(tmp_path, _artifact(winner=nulled, noise_gate=gate)),
        _protocol(),
        HASH,
        require_noise_gate=False,
    )
    value = candidate.search_sharpe if metric == "sharpe" else candidate.search_plateau_score
    assert value != value, "a null metric is NaN, not a refusal"


# --------------------------------------------------------------------------
# The config hash. A path names a file; the hash names what it said when it was
# searched.
# --------------------------------------------------------------------------

SEARCHED_CONFIG = b"name: s\nfixed: {size: 95}\nsearch: {fast: {min: 5, max: 30, step: 5}}\n"


def _searched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Stand in a temporary repository root holding one config and one artifact of it.

    The artifact names its config by a repository-relative path, so the working
    directory has to be the root that path resolves against.
    """
    monkeypatch.chdir(tmp_path)
    config = Path("configs") / "searched.yaml"
    config.parent.mkdir()
    config.write_bytes(SEARCHED_CONFIG)
    artifact = _artifact(config=config.as_posix(), config_content_hash=config_content_hash(config))
    return _written(tmp_path, artifact), config


def test_a_config_unchanged_since_the_search_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact, config = _searched(tmp_path, monkeypatch)

    candidate = read_candidate(artifact, _protocol(), HASH)

    assert candidate.config_path == config
    assert candidate.config_content_hash == config_content_hash(config)


@pytest.mark.parametrize(
    "edited",
    [
        SEARCHED_CONFIG.replace(b"max: 30", b"max: 40"),
        SEARCHED_CONFIG.replace(b"size: 95", b"size: 50"),
        SEARCHED_CONFIG + b"extra: 1\n",
    ],
    ids=["a-search-bound", "a-fixed-value", "an-added-key"],
)
def test_a_config_edited_after_the_search_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edited: bytes
) -> None:
    artifact, config = _searched(tmp_path, monkeypatch)
    config.write_bytes(edited)

    with pytest.raises(CandidateError, match="edited after the search"):
        read_candidate(artifact, _protocol(), HASH)


def test_a_config_only_reformatted_since_the_search_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows checkout of the same commit, with a comment added, is the same strategy."""
    artifact, config = _searched(tmp_path, monkeypatch)
    config.write_bytes(b"# reviewed\r\n" + SEARCHED_CONFIG.replace(b"\n", b"\r\n"))

    assert read_candidate(artifact, _protocol(), HASH).config_path == config


def test_a_config_that_has_gone_missing_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact, config = _searched(tmp_path, monkeypatch)
    config.unlink()

    with pytest.raises(CandidateError, match="cannot be hashed"):
        read_candidate(artifact, _protocol(), HASH)


def test_a_recorded_hash_that_is_simply_wrong_is_refused(tmp_path: Path) -> None:
    artifact = _written(tmp_path, _artifact(config_content_hash=OTHER_HASH))

    with pytest.raises(CandidateError, match="edited after the search"):
        read_candidate(artifact, _protocol(), HASH)


# --------------------------------------------------------------------------
# The noise gate. A winner is the cell selection chose; whether it may be
# validated is a separate answer, and this reader is where it is enforced.
# --------------------------------------------------------------------------

#: Gate objects for every status short of a pass, with counts that agree with it.
SHORT_OF_A_PASS: dict[str, dict[str, Any]] = {
    "not_computed": {"scoreable_trials": 0, "beaten": None, "required": None},
    "undetermined": {"scoreable_trials": 20, "beaten": 19, "required": 19},
    "failed": {"scoreable_trials": 100, "beaten": 12, "required": 95},
}


def test_a_candidate_that_passed_the_gate_carries_the_counts_it_passed_on(tmp_path: Path) -> None:
    candidate = read_candidate(_written(tmp_path, _artifact()), _protocol(), HASH)

    assert candidate.noise_gate == NoiseGate(
        status="passed",
        candidate_sharpe=0.6399777383485417,
        scoreable_trials=100,
        beaten=97,
        required=95,
    )


@pytest.mark.parametrize("status", sorted(SHORT_OF_A_PASS))
def test_a_candidate_short_of_a_pass_is_refused_before_anything_is_spent(
    tmp_path: Path, status: str
) -> None:
    artifact = _artifact(noise_gate=_gate(status=status, **SHORT_OF_A_PASS[status]))

    with pytest.raises(CandidateError, match="cannot be validated"):
        read_candidate(_written(tmp_path, artifact), _protocol(), HASH)


@pytest.mark.parametrize("status", sorted(SHORT_OF_A_PASS))
def test_the_paper_trader_may_still_read_a_candidate_short_of_a_pass(
    tmp_path: Path, status: str
) -> None:
    # Live trading tests the simulator, not the strategy, so it has every use
    # for a winner the gate refused. The caller has to ask for that by name.
    artifact = _artifact(noise_gate=_gate(status=status, **SHORT_OF_A_PASS[status]))

    candidate = read_candidate(
        _written(tmp_path, artifact), _protocol(), HASH, require_noise_gate=False
    )

    assert candidate.noise_gate.status == status
    assert candidate.noise_gate.beaten == SHORT_OF_A_PASS[status]["beaten"]


@pytest.mark.parametrize("require", [True, False])
@pytest.mark.parametrize(
    ("gate", "match"),
    [
        (None, "carries a null 'noise_gate'"),
        ("passed", "not an object"),
        (["passed"], "not an object"),
        ({"status": "passed"}, "carries no 'percentile'"),
    ],
    ids=["null", "a-string", "a-list", "status-only"],
)
def test_a_gate_that_is_not_the_recorded_object_is_refused(
    tmp_path: Path, gate: object, match: str, require: bool
) -> None:
    with pytest.raises(CandidateError, match=match):
        read_candidate(
            _written(tmp_path, _artifact(noise_gate=gate)),
            _protocol(),
            HASH,
            require_noise_gate=require,
        )


@pytest.mark.parametrize("require", [True, False])
def test_an_artifact_with_no_gate_at_all_is_refused(tmp_path: Path, require: bool) -> None:
    artifact = _artifact()
    del artifact["noise_gate"]

    with pytest.raises(CandidateError, match="carries no 'noise_gate'"):
        read_candidate(_written(tmp_path, artifact), _protocol(), HASH, require_noise_gate=require)


@pytest.mark.parametrize("require", [True, False])
@pytest.mark.parametrize("status", ["maybe", "PASSED", "", 1, True])
def test_a_status_outside_the_closed_set_is_refused(
    tmp_path: Path, status: object, require: bool
) -> None:
    with pytest.raises(CandidateError, match="status"):
        read_candidate(
            _written(tmp_path, _artifact(noise_gate=_gate(status=status))),
            _protocol(),
            HASH,
            require_noise_gate=require,
        )


@pytest.mark.parametrize("require", [True, False])
def test_a_gate_saying_there_was_no_candidate_beside_a_winner_is_refused(
    tmp_path: Path, require: bool
) -> None:
    gate = _gate(status="no_candidate", candidate_sharpe=None, beaten=None, required=None)

    with pytest.raises(CandidateError, match="cannot both be true"):
        read_candidate(
            _written(tmp_path, _artifact(noise_gate=gate)),
            _protocol(),
            HASH,
            require_noise_gate=require,
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"percentile": 90}, "judged under a different rule"),
        ({"min_trials": 20}, "judged under a different rule"),
        ({"scoreable_trials": 99}, "do not support"),
        ({"beaten": 94}, "do not support"),
        ({"scoreable_trials": 200}, "do not support"),
        ({"beaten": None}, "do not support"),
        ({"beaten": 97.5}, "not a whole number"),
        ({"scoreable_trials": "100"}, "not a whole number"),
        ({"beaten": True}, "not a whole number"),
        ({"beaten": -1}, "not a whole number"),
        ({"candidate_sharpe": 5.0}, "is not the winner's"),
        ({"candidate_sharpe": None}, "is not the winner's"),
    ],
    ids=[
        "another-percentile",
        "another-trial-floor",
        "one-trial-short",
        "one-surrogate-short",
        "97-of-200-is-not-95-percent",
        "no-count-at-all",
        "a-fractional-count",
        "a-count-as-text",
        "a-boolean-count",
        "a-negative-count",
        "a-sharpe-the-winner-does-not-have",
        "no-sharpe",
    ],
)
def test_a_pass_its_own_numbers_do_not_support_is_refused(
    tmp_path: Path, overrides: dict[str, object], match: str
) -> None:
    # A hand-edited status, or a verdict reached under a rule this build no
    # longer applies. Either way the word "passed" is not what gets trusted.
    artifact = _artifact(noise_gate=_gate(**overrides))

    with pytest.raises(CandidateError, match=match):
        read_candidate(_written(tmp_path, artifact), _protocol(), HASH)
