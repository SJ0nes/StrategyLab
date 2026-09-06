"""Reading the candidate a search nominated, out of the artifact that nominated it.

WHY THE VALIDATOR READS A SEARCH ARTIFACT RATHER THAN A CANDIDATE FILE is
settled in ``cli.validate``'s docstring and not repeated here. What this module
adds is the enforcement: the checks below are what make "the chain is honest end
to end" a property of the code rather than a claim in help text.

THREE CROSS-CHECKS, and each of them refuses to spend a touch on a validation
that could not be interpreted afterwards.

**The schema version must match.** A field this reader does not know about is a
field the search recorded and the verdict would drop.

**The protocol version must match.** Every threshold the verdict is measured
against comes from the protocol in force NOW; the parameters come from a search
run under the protocol THEN. If those are different documents, the verdict is a
comparison between two rulebooks.

**The snapshot hash must match.** Vendors revise history. A candidate chosen on
one snapshot and validated on another is not the same candidate, and the
difference is invisible in every number either run reports.

All three refuse rather than warn. A warning would be read after the counter had
already moved, which is the one moment in this project where "you can always run
it again" is false.

NOTHING HERE DEFAULTS A MISSING FIELD. Every value this module returns is
either present in the artifact or the artifact is refused, because a default
would be written into the verdict as though the search had stated it - and one
of the fields this once defaulted, ``degenerate``, defaulted in the unsafe
direction.

Absent and NULL are both refused, and for the two metrics they are told apart.
``sharpe`` and ``plateau_score`` may legitimately be ``null``: that is how
:func:`write_search_result` records a non-finite figure, and it comes back as
``NaN``. A MISSING key for either is refused, because "the search recorded a
non-finite score" and "the search never recorded a score" must not read alike
in a verdict where both would appear as ``null``.

WHAT IS NOT CHECKED, and it is a real hole: the strategy CONFIG is named by path
and compared by nothing. Only the price snapshot is hashed. A config edited
between the search and the validation loads silently, and the verdict would
describe a strategy no search ever nominated. ``LIMITATIONS.md`` records it.
What IS checked about that path is that it is relative, because a verdict
records it and an absolute path carries a username.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from strategylab.protocol import Protocol
from strategylab.search.loop import SEARCH_SCHEMA_VERSION


class CandidateError(Exception):
    """A search artifact carries no candidate this protocol can validate.

    Distinct from a validation that ran and failed. This means no validation was
    attempted, and - importantly - no touch was charged.
    """


@dataclass(frozen=True)
class CandidateRef:
    """One search's winner, and the trail back to what produced it.

    Every field here is copied into the validation artifact, so a verdict can be
    traced to a search, a snapshot and a config without opening a second file.

    Attributes:
        source: Path of the search artifact this was read from, as given.
        strategy: ``definition.name`` from the search.
        config_path: Repository-relative path of the strategy config the search
            loaded.
        params: The winning parameter combination, mapping placeholder name to
            a value in BARS.
        data_content_hash: Content hash of the price snapshot, which this
            reader has already checked matches the one the search ran against.
        protocol_version: Schema version of the protocol, likewise already
            checked against the one the search ran under.
        search_sharpe: The candidate's own in-sample Sharpe, a dimensionless
            ratio. Recorded so the held-back figure can be read beside the
            in-sample one it is expected to fall short of.
        search_plateau_score: Median in-sample Sharpe across the candidate's
            neighbourhood, in the same units as ``search_sharpe``. THIS is what
            the candidate was chosen on, not ``search_sharpe``.
        degenerate: Whether the search flagged the candidate's plateau as
            insensitivity rather than robustness.
        noise_ceiling_note: The search's own sentence about what its noise
            ceiling does or does not support, copied verbatim. A search run
            without ``--trials`` says so here, and that sentence travels into
            the verdict rather than being dropped at the boundary.
    """

    source: Path
    strategy: str
    config_path: Path
    params: dict[str, int]
    data_content_hash: str
    protocol_version: int
    search_sharpe: float
    search_plateau_score: float
    degenerate: bool
    noise_ceiling_note: str


def _params(raw: Any) -> dict[str, int]:
    """Coerce a winner's ``params`` object to a mapping of name to whole bars.

    Args:
        raw: The parsed ``winner.params`` value.

    Returns:
        The parameter combination, every value an ``int`` in bars.

    Raises:
        CandidateError: If it is not a mapping, is empty, or holds a value that
            is not a whole number of bars of AT LEAST ONE. Zero and negative are
            whole numbers and are refused: every searched parameter this project
            can produce is a lookback, because the config loader refuses a
            searched parameter no condition uses as a period. The day that stops
            being true - a threshold, a fraction, a count that may legitimately
            be zero - this refusal starts rejecting artifacts the engine would
            have run, and its message asserts a fact about the parameter that
            this module cannot check.
    """
    if not isinstance(raw, dict) or not raw:
        raise CandidateError("the winner in this artifact carries no 'params' mapping")
    resolved: dict[str, int] = {}
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
            raise CandidateError(
                f"the winner's parameter '{name}' is {value!r}, which is not a whole number of bars"
            )
        # AT LEAST ONE BAR, not merely whole. Zero and negative are whole
        # numbers and would pass a check for integrality alone, then fail much
        # later inside `required_history` as a ValueError - which the CLI
        # reports as "validation could not run" rather than "nothing to
        # validate". The distinction is the whole point of this module: an
        # uninterpretable candidate is refused here, by name, before anything
        # is spent.
        if value < 1:
            raise CandidateError(
                f"the winner's parameter '{name}' is {value!r}; a lookback is at least one bar"
            )
        resolved[str(name)] = int(value)
    return resolved


def _required(mapping: dict[str, Any], key: str, where: str, path: Path) -> Any:
    """Fetch a field that must be present AND non-null, or refuse naming it.

    THE ALTERNATIVE WAS A DEFAULT, and a default here is the thing this module
    exists to refuse. Three fields were originally read with one: ``strategy``
    fell back to ``""``, ``degenerate`` to ``False`` and ``noise_ceiling_note``
    to ``""``. The middle one defaults in the UNSAFE direction - a missing flag
    reads as "the search did not flag this plateau" - and the last silently
    drops the sentence this module's docstring promises travels into the
    verdict.

    NULL IS REFUSED AS WELL AS ABSENT, and the first version of this helper
    refused only absence - which moved the fault rather than removing it.
    ``bool(None)`` is ``False``, so ``"degenerate": null`` reproduced the exact
    unsafe default in a different shape; ``str(None)`` is ``"None"``, so a null
    strategy name would have been recorded as the literal string ``None``.

    Args:
        mapping: The object the field lives in - the artifact for a top-level
            field, the winner object for one of its own.
        key: The field that must be present and non-null.
        where: Human-readable name of that object, for the message, so a reader
            knows whether to look at the top level or inside ``winner``.
        path: Path of the artifact, for the message.

    Returns:
        The value stored at ``key``, never ``None``.

    Raises:
        CandidateError: If ``key`` is absent or its value is ``None``.
    """
    if key not in mapping or mapping[key] is None:
        state = "carries no" if key not in mapping else "carries a null"
        raise CandidateError(
            f"search artifact '{path.as_posix()}' {state} '{key}' in its {where}. This "
            "reader refuses a missing or null field rather than defaulting it: a default "
            "would be recorded in the verdict as though the search had stated it"
        )
    return mapping[key]


def _number(winner: dict[str, Any], key: str, path: Path) -> float:
    """Read one of the winner's metrics, distinguishing a null from an absence.

    THESE TWO CASES ARE NOT THE SAME and the first version of this function
    conflated them. A metric written as ``null`` is a non-finite value
    :func:`write_search_result` deliberately made JSON-safe, and it comes back
    as ``NaN``. A metric whose KEY IS MISSING is a search that never recorded
    the figure at all, and that is refused - otherwise
    ``search_plateau_score``, the field the candidate was actually selected on,
    could arrive as ``NaN`` and be written into the verdict as ``null``,
    indistinguishable from a score the search recorded as non-finite on purpose.

    Args:
        winner: The parsed winner object.
        key: The metric to read.
        path: Path of the artifact, for the message.

    Returns:
        The value as a float, or ``NaN`` when the key is present and ``null``.

    Raises:
        CandidateError: If the key is absent, or its value is present and not a
            number.
    """
    if key not in winner:
        raise CandidateError(
            f"search artifact '{path.as_posix()}' carries no '{key}' in its winner. A "
            "null is a figure the search recorded as non-finite; an absent key is a "
            "figure it never recorded, and the two must not read alike"
        )
    raw = winner[key]
    if raw is None:
        return float("nan")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CandidateError(f"the winner's '{key}' is {raw!r}, which is not a number")
    return float(raw)


def read_candidate(path: Path, protocol: Protocol, data_content_hash: str) -> CandidateRef:
    """Read a search artifact's winner, refusing anything this protocol cannot judge.

    Args:
        path: The search artifact, such as
            ``state/search-20260822T213227Z.json``.
        protocol: The protocol in force, checked against the one the search ran
            under.
        data_content_hash: Content hash of the price snapshot now cached,
            checked against the one the search ran against. Comes from
            :class:`~strategylab.data.source.CacheEntry`.

    Returns:
        The candidate, with the trail back to the search that chose it.

    Raises:
        CandidateError: On every refusal this module makes, which is every way
            an artifact can fail to name a candidate this protocol could judge:
            the file cannot be read, is not JSON, or is not an object; it
            carries an unfamiliar ``schema_version``; it nominated no winner, or
            a ``winner`` that is not an object; it was run under a different
            protocol version, or against a different price snapshot; it names no
            config, or names one by a path that is absolute under either POSIX
            or Windows rules; it omits or nulls ``strategy``, ``degenerate`` or
            ``noise_ceiling_note``, or carries a ``degenerate`` that is not a
            boolean; it omits ``sharpe`` or ``plateau_score`` from the winner
            entirely, as opposed to recording either as ``null``; the winner
            carries no ``params`` mapping, or a parameter that is not a whole
            number of bars of at least one; or a winner metric is present and
            not a number. Nothing here charges a touch, so every one of these
            refusals is free.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CandidateError(f"cannot read search artifact '{path.as_posix()}': {exc}") from exc
    try:
        artifact = json.loads(text)
    except ValueError as exc:
        raise CandidateError(
            f"search artifact '{path.as_posix()}' is not valid JSON: {exc}"
        ) from exc
    if not isinstance(artifact, dict):
        raise CandidateError(
            f"search artifact '{path.as_posix()}' is not a JSON object; "
            f"it is a {type(artifact).__name__}"
        )

    version = artifact.get("schema_version")
    if version != SEARCH_SCHEMA_VERSION:
        raise CandidateError(
            f"search artifact '{path.as_posix()}' is schema version {version!r}, and this "
            f"build reads version {SEARCH_SCHEMA_VERSION}. Re-run the search rather than "
            "validating a candidate read out of a shape this code does not know"
        )

    winner = artifact.get("winner")
    if winner is None:
        raise CandidateError(
            f"search artifact '{path.as_posix()}' nominated no candidate - every cell was "
            "rejected. There is nothing to walk forward, and the held-back window is too "
            "expensive to spend finding that out"
        )
    if not isinstance(winner, dict):
        raise CandidateError(
            f"search artifact '{path.as_posix()}' carries a 'winner' that is not an object"
        )

    found_version = artifact.get("protocol_version")
    if found_version != protocol.protocol_version:
        raise CandidateError(
            f"search artifact '{path.as_posix()}' was run under protocol version "
            f"{found_version!r} and the protocol in force is version "
            f"{protocol.protocol_version}. The thresholds a verdict is measured against "
            "would come from a different document than the parameters it judges"
        )

    found_hash = artifact.get("data_content_hash")
    if found_hash != data_content_hash:
        raise CandidateError(
            f"search artifact '{path.as_posix()}' was run against snapshot "
            f"{str(found_hash)[:12]} and the cached snapshot is {data_content_hash[:12]}. "
            "Vendors revise history, so this candidate was chosen on prices that are no "
            "longer the ones on disk; re-run the search"
        )

    config = artifact.get("config")
    if not isinstance(config, str) or not config:
        raise CandidateError(f"search artifact '{path.as_posix()}' names no config")
    config_path = Path(config)
    # The write side already refuses an absolute config path, because a search
    # artifact is a file this project's documentation invites strangers to open
    # and an absolute path on most machines carries a username. That refusal is
    # in another module, and this one states relativeness as a property of the
    # value it RETURNS - a value then written verbatim into the validation
    # artifact. A promise enforced one module away is not enforced.
    #
    # BOTH FLAVOURS, because `Path` is the host's. On Windows,
    # `Path("/home/<user>/x.yaml").is_absolute()` is False; on POSIX,
    # `Path("C:\\Users\\<user>\\x.yaml").is_absolute()` is False. An artifact
    # written on one platform and validated on the other would otherwise carry
    # exactly the username this guard exists to stop.
    windows_form = PureWindowsPath(config)
    if (
        PurePosixPath(config).is_absolute()
        or windows_form.is_absolute()
        # DRIVE-RELATIVE, which `is_absolute()` calls relative: "C:configs/x.yaml"
        # has a drive and no root, so PureWindowsPath says False while
        # `ntpath.isabs` says True. It still resolves against a drive's current
        # directory rather than against this repository.
        or windows_form.drive != ""
    ):
        raise CandidateError(
            f"search artifact '{path.as_posix()}' names its config by the absolute path "
            f"'{config}'. A verdict records that path, and an absolute path on most "
            "machines carries a username into a file this repository may publish"
        )
    # REPOSITORY-RELATIVE IS THE PROMISE, and "not absolute" is weaker than it.
    # `../../Users/<name>/configs/x.yaml` is relative under both flavours,
    # passes every check above, and carries exactly the username those checks
    # exist to stop - into the verdict, verbatim.
    if ".." in windows_form.parts or ".." in PurePosixPath(config).parts:
        raise CandidateError(
            f"search artifact '{path.as_posix()}' names its config as '{config}', which "
            "climbs out of the repository with '..'. A verdict records the path verbatim, "
            "and a path that escapes upwards can name anything on the machine"
        )

    # NOTHING IS COERCED, and an earlier version type-checked only this one.
    # `bool("false")` is True, so a flag written as a string would read as
    # "flagged" or "not flagged" by truthiness alone - but `str(["ma"])` is
    # `"['ma']"` just as silently, and `protocol.py` already took this project's
    # position on that exact coercion: it "deliberately does NOT accept a list,
    # mapping, or None by stringifying it", because such a value fails much
    # later somewhere that names neither the field nor the file.
    degenerate = _required(winner, "degenerate", "winner", path)
    if not isinstance(degenerate, bool):
        raise CandidateError(
            f"search artifact '{path.as_posix()}' carries a 'degenerate' of {degenerate!r}, "
            "which is not a boolean; coercing it would decide the plateau flag by "
            "truthiness rather than by what the search found"
        )
    strings: dict[str, str] = {}
    for key, where in (("strategy", "top level"), ("noise_ceiling_note", "top level")):
        value = _required(artifact, key, where, path)
        if not isinstance(value, str):
            raise CandidateError(
                f"search artifact '{path.as_posix()}' carries a '{key}' of {value!r}, which "
                "is not a string. Stringifying it would record a list or a number in the "
                "verdict as though it were the text the search wrote"
            )
        strings[key] = value

    return CandidateRef(
        source=path,
        strategy=strings["strategy"],
        config_path=config_path,
        params=_params(winner.get("params")),
        data_content_hash=data_content_hash,
        protocol_version=protocol.protocol_version,
        search_sharpe=_number(winner, "sharpe", path),
        search_plateau_score=_number(winner, "plateau_score", path),
        degenerate=degenerate,
        noise_ceiling_note=strings["noise_ceiling_note"],
    )
