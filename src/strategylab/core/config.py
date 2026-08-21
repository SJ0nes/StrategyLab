"""The strategy-config loader: the only reader of the files in ``configs/``.

A strategy config is declarative and stays that way. Its conditions are a FLAT
list combined with AND, drawn from a vocabulary this module and
:func:`strategylab.core.strategy.evaluate` both know. There is no nesting, no
OR, no expression to evaluate, and nothing looked up dynamically at load time.
Wanting more than that is a code change with a test - a new name in the
vocabulary below - and never a new shape in the YAML.

Validation is total and it happens here, once, before anything runs. A config
that half-loads is worse than one that refuses to load: the search that follows
takes hours, and a condition silently dropped for a typo produces a result that
looks fine and means nothing. A failure in a field names that field by its dotted
path, so ``entry[1].target.period`` sends the operator to a line rather than back
through the file; a failure in the file itself - missing, unreadable, unparseable
- names the file, because there is no field to name.

What this module does NOT do is resolve ``$placeholder`` periods. It checks that
every placeholder has somewhere to get a value from, and that every searched
parameter is actually used, but the substitution itself belongs with the grid,
which does not exist yet. A :class:`StrategyDef` returned from here is validated
and not yet evaluable.

One honest caveat about the vocabulary. The rule is that every name in it is one
the engine implements and tests. ``evaluate`` is still a stub, so today the
vocabulary is what the engine WILL be built against rather than a description of
running code, and this module is the only place either name is enforced.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import yaml

from strategylab.core.types import Condition, IndicatorRef, StrategyDef

# The schema this loader understands. A file declaring anything else is refused
# rather than read under rules that may no longer be its own.
SUPPORTED_VERSION = 1

# The indicator vocabulary, mapping each name to whether it takes a lookback
# period in bars. Closed on purpose: an unknown name is a hard error, never a
# warning and never a silent skip.
INDICATORS: dict[str, bool] = {
    "close": False,
    "sma": True,
}

# The comparison vocabulary. ``above`` tests the current bar; the two crossing
# operators compare the current bar against the one before it, which is why a
# strategy using either needs one bar of history beyond its longest lookback.
# ``below`` is deliberately absent: adding it means a case in ``evaluate``, a
# test, and an entry here, which is the rule working rather than an oversight.
OPERATORS: frozenset[str] = frozenset({"above", "crosses_above", "crosses_below"})

# A strategy name and a parameter name both end up in filenames and in result
# keys, so both are held to a charset that is safe in either.
#
# The anchors are \A and \Z rather than ^ and $ deliberately. Python's $ also
# matches immediately BEFORE a single trailing newline, so `^[A-Za-z0-9_-]+$`
# accepts "ma_crossover\n" - which a YAML block scalar produces without anyone
# meaning to - and a newline would then travel into a filename. \Z has no such
# concession.
_NAME_PATTERN = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_TOP_LEVEL_KEYS = frozenset({"name", "version", "description", "entry", "exit", "search", "fixed"})
_CONDITION_KEYS = frozenset({"indicator", "period", "operator", "target"})
_TARGET_KEYS = frozenset({"indicator", "period"})
_RANGE_KEYS = frozenset({"min", "max", "step"})


class StrategyConfigError(Exception):
    """Raised when a strategy config is missing, malformed, or self-inconsistent.

    Where the failure is in a field, the message names that field by its dotted
    path, so the operator can go straight to the line rather than re-reading the
    whole file. Where the failure is the file's own - absent, undecodable, not
    YAML, empty - it names the file instead. A caller must not assume a dotted
    path can be parsed out of every message.
    """


class _NoDuplicateKeyLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that refuses a mapping with a repeated key.

    PyYAML's default is to keep the last of a repeated key and say nothing. In a
    file whose whole purpose is to be read literally that is the worst available
    behaviour: a second ``entry:`` block silently discards the first, and the
    search then runs a strategy nobody wrote.
    """

    def construct_mapping(self, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
        """Construct a mapping, rejecting any key that appears more than once.

        Args:
            node: The mapping node being constructed.
            deep: Whether to construct child nodes eagerly, as PyYAML defines it.

        Returns:
            The constructed mapping.

        Raises:
            yaml.constructor.ConstructorError: If a key is repeated.
        """
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError:
                # Unhashable: the base class raises PyYAML's own error for it below.
                continue
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found a duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep)


def _fail(source: str, where: str, problem: str) -> StrategyConfigError:
    """Build the one error shape this module raises.

    Args:
        source: Display name of the config file, used as the message prefix.
        where: Dotted path of the offending field, or ``""`` for the file itself.
        problem: What is wrong with it.

    Returns:
        The error, for the caller to raise.
    """
    if not where:
        return StrategyConfigError(f"{source}: {problem}")
    return StrategyConfigError(f"{source}: field '{where}' {problem}")


def _require(mapping: dict[str, Any], key: str, source: str, where: str) -> Any:
    """Return a required key's value, or raise naming the field.

    Args:
        mapping: The parsed mapping to read from.
        key: The key that must be present.
        source: Display name of the config file.
        where: Dotted path of ``mapping``, or ``""`` at the root.

    Returns:
        The value at ``key``.

    Raises:
        StrategyConfigError: If ``key`` is absent.
    """
    if key not in mapping:
        path = f"{where}.{key}" if where else key
        raise _fail(source, path, "is required and is missing")
    return mapping[key]


def _reject_unknown_keys(
    mapping: dict[str, Any], known: frozenset[str], source: str, where: str
) -> None:
    """Raise if the mapping carries a key outside the closed schema.

    A misspelled key is the failure this exists for. ``entires:`` alongside a
    valid ``entry:`` would otherwise load as a strategy with no entry rule at
    all, and nothing downstream would know to complain.

    Args:
        mapping: The parsed mapping to check.
        known: Every key the schema allows here.
        source: Display name of the config file.
        where: Dotted path of ``mapping``.

    Raises:
        StrategyConfigError: If any key is not in ``known``.
    """
    unknown = sorted(str(key) for key in mapping if key not in known)
    if unknown:
        raise _fail(
            source,
            where,
            f"carries unknown keys {unknown}; this schema allows only {sorted(known)}",
        )


def _as_mapping(value: Any, source: str, where: str) -> dict[str, Any]:
    """Coerce a parsed YAML value to a mapping, or raise naming the field.

    Args:
        value: The parsed value.
        source: Display name of the config file.
        where: Dotted path of the field.

    Returns:
        The value as a ``dict``.

    Raises:
        StrategyConfigError: If ``value`` is not a mapping.
    """
    if not isinstance(value, dict):
        raise _fail(source, where, f"must be a mapping, got {type(value).__name__}")
    return value


def _as_list(value: Any, source: str, where: str) -> list[Any]:
    """Coerce a parsed YAML value to a list, or raise naming the field.

    Args:
        value: The parsed value.
        source: Display name of the config file.
        where: Dotted path of the field.

    Returns:
        The value as a ``list``.

    Raises:
        StrategyConfigError: If ``value`` is not a list.
    """
    if not isinstance(value, list):
        raise _fail(source, where, f"must be a list, got {type(value).__name__}")
    return value


def _as_str(value: Any, source: str, where: str) -> str:
    """Coerce a parsed YAML value to a string, or raise naming the field.

    Does not stringify a list or a mapping. A name written as ``[trend]`` would
    otherwise become the literal ``"['trend']"`` and surface much later, in a
    filename, as something nobody typed.

    Args:
        value: The parsed value.
        source: Display name of the config file.
        where: Dotted path of the field.

    Returns:
        The value as a ``str``.

    Raises:
        StrategyConfigError: If ``value`` is not a string.
    """
    if not isinstance(value, str):
        raise _fail(source, where, f"must be a string, got {type(value).__name__}")
    return value


def _as_int(value: Any, source: str, where: str) -> int:
    """Coerce a parsed YAML value to an int, or raise naming the field.

    ``bool`` is excluded: YAML's ``true`` is an ``int`` to Python, and a period
    of ``True`` is a one-bar lookback that nobody asked for.

    Args:
        value: The parsed value.
        source: Display name of the config file.
        where: Dotted path of the field.

    Returns:
        The value as an ``int``.

    Raises:
        StrategyConfigError: If ``value`` is not an integer.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(source, where, f"must be an integer, got {type(value).__name__}")
    return value


def _as_float(value: Any, source: str, where: str) -> float:
    """Coerce a parsed YAML value to a finite float, or raise naming the field.

    Finiteness is checked here rather than downstream. YAML spells ``.inf`` and
    ``.nan`` plainly, and either one reaching a grid bound produces a range that
    never terminates or a comparison that is silently false forever.

    Args:
        value: The parsed value.
        source: Display name of the config file.
        where: Dotted path of the field.

    Returns:
        The value as a ``float``.

    Raises:
        StrategyConfigError: If ``value`` is not a real number, or is not finite.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(source, where, f"must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise _fail(source, where, f"must be a finite number, got {number}")
    return number


def _as_name(value: Any, source: str, where: str) -> str:
    """Coerce a parsed YAML value to an identifier this project can put in a path.

    Args:
        value: The parsed value.
        source: Display name of the config file.
        where: Dotted path of the field.

    Returns:
        The value as a ``str``, known to match ``[A-Za-z0-9_-]+``.

    Raises:
        StrategyConfigError: If ``value`` is not a string, is empty, or carries a
            character that is not a letter, digit, underscore, or hyphen.
    """
    name = _as_str(value, source, where)
    if not _NAME_PATTERN.match(name):
        raise _fail(
            source,
            where,
            f"must be letters, digits, underscores or hyphens, got {name!r}; "
            "it becomes part of a filename and of a result key",
        )
    return name


def _placeholder(raw: str, source: str, where: str) -> str:
    """Read a ``$placeholder`` period and return the parameter name behind it.

    Args:
        raw: The string as written in the config, expected to start with ``$``.
        source: Display name of the config file.
        where: Dotted path of the field.

    Returns:
        The parameter name, without the leading ``$``.

    Raises:
        StrategyConfigError: If ``raw`` does not start with ``$``, or what
            follows is not a valid parameter name.
    """
    if not raw.startswith("$"):
        raise _fail(source, where, f"must be a whole number of bars or a $placeholder, got {raw!r}")
    return _as_name(raw[1:], source, where)


def _period(raw: Any, source: str, where: str) -> tuple[int | None, str | None]:
    """Read a period, which is either a literal count of bars or a placeholder.

    Args:
        raw: The parsed value of the ``period`` key.
        source: Display name of the config file.
        where: Dotted path of the field.

    Returns:
        A pair of ``(period, period_param)`` in which exactly one is set. The
        period, when set, is a count of bars.

    Raises:
        StrategyConfigError: If the value is ``None``, or is neither a positive
            whole number of bars nor a well-formed ``$placeholder``.
    """
    if raw is None:
        raise _fail(source, where, "is empty; it must be a count of bars or a $placeholder")
    if isinstance(raw, str):
        return None, _placeholder(raw, source, where)
    bars = _as_int(raw, source, where)
    if bars < 1:
        raise _fail(source, where, f"must be at least 1 bar, got {bars}")
    return bars, None


def _indicator_ref(mapping: dict[str, Any], source: str, where: str) -> IndicatorRef:
    """Build one :class:`IndicatorRef` from a parsed mapping.

    Args:
        mapping: The parsed mapping, carrying ``indicator`` and optionally
            ``period``.
        source: Display name of the config file.
        where: Dotted path of ``mapping``.

    Returns:
        The reference, its period either a literal count of bars or an
        unresolved placeholder name.

    Presence of ``period`` is decided by whether the KEY is there, not by
    whether its value is non-null. ``period: null`` is a period written down, and
    reporting it as missing sends the operator looking for a line that is in
    front of them.

    Raises:
        StrategyConfigError: If the indicator name is absent or outside the
            vocabulary, or if the ``period`` key is present on an indicator that
            takes none, absent from one that requires it, or malformed.
    """
    name = _as_str(_require(mapping, "indicator", source, where), source, f"{where}.indicator")
    if name not in INDICATORS:
        raise _fail(
            source,
            f"{where}.indicator",
            f"is not in the indicator vocabulary: {name!r} (known: {sorted(INDICATORS)})",
        )

    takes_period = INDICATORS[name]
    written = "period" in mapping
    if takes_period and not written:
        raise _fail(source, f"{where}.period", f"is required by indicator {name!r} and is missing")
    if not takes_period and written:
        raise _fail(source, f"{where}.period", f"is not accepted by indicator {name!r}")
    if not written:
        return IndicatorRef(indicator=name)

    period, period_param = _period(mapping["period"], source, f"{where}.period")
    return IndicatorRef(indicator=name, period=period, period_param=period_param)


def _condition(raw: Any, source: str, where: str) -> Condition:
    """Build one :class:`Condition` from a parsed mapping.

    The right-hand side is either another indicator or a plain number. It is
    never a further condition: a ``target`` carrying an ``operator`` is nesting,
    which this format does not have, and it is refused rather than ignored.

    Args:
        raw: The parsed condition mapping.
        source: Display name of the config file.
        where: Dotted path of the condition, such as ``entry[0]``.

    Returns:
        The parsed condition.

    Raises:
        StrategyConfigError: If the condition is not a mapping, carries a key
            outside the schema, omits a required key, or names an operator
            outside the vocabulary.
    """
    mapping = _as_mapping(raw, source, where)
    _reject_unknown_keys(mapping, _CONDITION_KEYS, source, where)

    left = _indicator_ref(mapping, source, where)

    operator = _as_str(_require(mapping, "operator", source, where), source, f"{where}.operator")
    if operator not in OPERATORS:
        raise _fail(
            source,
            f"{where}.operator",
            f"is not in the operator vocabulary: {operator!r} (known: {sorted(OPERATORS)})",
        )

    raw_target = _require(mapping, "target", source, where)
    right: IndicatorRef | float
    if isinstance(raw_target, dict):
        _reject_unknown_keys(raw_target, _TARGET_KEYS, source, f"{where}.target")
        right = _indicator_ref(raw_target, source, f"{where}.target")
    else:
        right = _as_float(raw_target, source, f"{where}.target")

    return Condition(left=left, operator=operator, right=right)


def _conditions(raw: Any, source: str, where: str) -> tuple[Condition, ...]:
    """Build one flat, AND-combined condition list.

    Args:
        raw: The parsed list.
        source: Display name of the config file.
        where: Dotted path of the list, ``entry`` or ``exit``.

    Returns:
        The conditions, in the order the file gives them.

    Raises:
        StrategyConfigError: If the value is not a list, is empty, or any
            condition in it is invalid. An empty list is refused because a rule
            no condition constrains fires on every bar.
    """
    items = _as_list(raw, source, where)
    if not items:
        raise _fail(source, where, "must hold at least one condition; an empty list fires always")
    return tuple(_condition(item, source, f"{where}[{index}]") for index, item in enumerate(items))


def _search(raw: Any, source: str) -> dict[str, dict[str, float]]:
    """Build the searched-parameter ranges.

    Checks this function does NOT make: that a bound is a whole number, or that
    it is at least one bar. Whether a parameter is a lookback at all depends on
    how the conditions reference it, which is not known here.
    :func:`_check_parameters` applies that rule once the reference walk has run.

    The order matters and is relied on by the failure messages: all three bounds
    are read and checked for finiteness BEFORE any of them are compared to each
    other, so ``max: -.inf`` is reported as a non-finite bound rather than as a
    max below its min.

    Args:
        raw: The parsed ``search`` mapping.
        source: Display name of the config file.

    Returns:
        Each range keyed by parameter name without the leading ``$``, carrying
        ``min``, ``max`` and ``step`` in the parameter's own units - bars, for a
        lookback period.

    Raises:
        StrategyConfigError: If ``search`` is not a mapping, a parameter name is
            malformed, a range omits a key or carries an unknown one, a bound is
            not a finite number, ``step`` is not positive, or ``max`` is below
            ``min``.
    """
    mapping = _as_mapping(raw, source, "search")
    ranges: dict[str, dict[str, float]] = {}
    for key, value in mapping.items():
        name = _as_name(key, source, "search")
        where = f"search.{name}"
        spec = _as_mapping(value, source, where)
        _reject_unknown_keys(spec, _RANGE_KEYS, source, where)
        low = _as_float(_require(spec, "min", source, where), source, f"{where}.min")
        high = _as_float(_require(spec, "max", source, where), source, f"{where}.max")
        step = _as_float(_require(spec, "step", source, where), source, f"{where}.step")
        if step <= 0:
            raise _fail(source, f"{where}.step", f"must be greater than 0, got {step}")
        if high < low:
            raise _fail(source, f"{where}.max", f"is below 'min' ({high} < {low})")
        ranges[name] = {"min": low, "max": high, "step": step}
    return ranges


def _fixed(raw: Any, source: str) -> dict[str, float]:
    """Build the fixed, never-searched parameters.

    Args:
        raw: The parsed ``fixed`` mapping.
        source: Display name of the config file.

    Returns:
        Each parameter keyed by name, its value a finite number in that
        parameter's own units.

    Raises:
        StrategyConfigError: If ``fixed`` is not a mapping, a name is malformed,
            or a value is not a finite number.
    """
    mapping = _as_mapping(raw, source, "fixed")
    values: dict[str, float] = {}
    for key, value in mapping.items():
        name = _as_name(key, source, "fixed")
        values[name] = _as_float(value, source, f"fixed.{name}")
    return values


def _referenced_params(definition: StrategyDef) -> dict[str, str]:
    """Collect every ``$placeholder`` the definition's conditions reference.

    Args:
        definition: The parsed strategy, its condition lists already built.

    Returns:
        Each referenced parameter name mapped to the dotted path of the first
        field that references it, so a failure can name a line in the file.
    """
    seen: dict[str, str] = {}
    for list_name, conditions in (("entry", definition.entry), ("exit", definition.exit)):
        for index, condition in enumerate(conditions):
            where = f"{list_name}[{index}]"
            sides = [(condition.left, f"{where}.period")]
            if isinstance(condition.right, IndicatorRef):
                sides.append((condition.right, f"{where}.target.period"))
            for ref, path in sides:
                if ref.period_param is not None:
                    seen.setdefault(ref.period_param, path)
    return seen


def _whole_bars(value: float, source: str, where: str, because: str) -> None:
    """Raise unless a value that will become a lookback is a whole count of bars.

    A period is an index offset into a frame of bars. Half a bar does not exist,
    and neither does a lookback of minus ten, so a value that fails this is not a
    parameter the grid can walk - it is a config the search would have to round
    or refuse at some later point, silently and per cell.

    Args:
        value: The value to check, in bars. Already known to be finite.
        source: Display name of the config file.
        where: Dotted path of the field, used in the message.
        because: Clause naming why this value must be a whole count of bars,
            interpolated into the message.

    Raises:
        StrategyConfigError: If ``value`` is below 1 or is not a whole number.
    """
    if value < 1 or value != int(value):
        raise _fail(
            source,
            where,
            f"{because}, so it must be a whole number of bars of at least 1, got {value}",
        )


def _check_parameters(definition: StrategyDef, source: str) -> None:
    """Check that placeholders and declared parameters account for each other.

    Three failures live here, and each of them produces a search that runs to
    completion and means nothing. A placeholder with no parameter behind it
    cannot be resolved. A searched parameter no condition mentions multiplies the
    grid - and with it the number of chances to fit noise - while changing no
    result. A name in both blocks is a parameter that is fixed and searched at
    once, and nothing can say which value won.

    This is also where a parameter's value meets what the conditions use it FOR.
    ``_search`` and ``_fixed`` validate each block in isolation and cannot know
    that ``fast`` is a lookback; only the reference walk knows that. So the
    whole-bars rule is enforced here, on both blocks, rather than in either one.

    Args:
        definition: The parsed strategy, its ``search`` and ``fixed`` blocks
            already validated in isolation.
        source: Display name of the config file.

    Raises:
        StrategyConfigError: On any of the three, and on a searched or fixed
            parameter that supplies a period whose value is not a whole number of
            bars of at least 1.
    """
    both = sorted(set(definition.search) & set(definition.fixed))
    if both:
        raise _fail(source, "fixed", f"repeats searched parameters {both}; a name belongs to one")

    references = _referenced_params(definition)
    for name in sorted(references):
        if name in definition.search:
            # Every bound of the range, not just its start: a fractional step
            # walks off the whole numbers after the first cell, and a range
            # ending below 1 bar is a grid of lookbacks that cannot be taken.
            because = f"bounds the range for ${name}, which supplies a lookback period"
            for bound in ("min", "max", "step"):
                _whole_bars(
                    definition.search[name][bound], source, f"search.{name}.{bound}", because
                )
            continue
        if name in definition.fixed:
            _whole_bars(
                definition.fixed[name], source, f"fixed.{name}", "supplies a lookback period"
            )
            continue
        raise _fail(
            source,
            references[name],
            f"references ${name}, which is in neither 'search' nor 'fixed'",
        )

    unused = sorted(set(definition.search) - set(references))
    if unused:
        raise _fail(
            source,
            "search",
            f"declares parameters {unused} that no condition references; each one "
            "multiplies the grid without changing a result",
        )


def load_strategy(path: Path) -> StrategyDef:
    """Load and validate one strategy config.

    This is the only reader of the files in ``configs/``; everything else in the
    system takes a :class:`StrategyDef`. Validation is complete before this
    returns: the schema is closed, conditions are flat and AND-combined, every
    indicator and operator is in the vocabulary this module declares, every
    ``$placeholder`` has a parameter behind it, every searched parameter is used,
    and every parameter a condition uses as a period is a whole number of bars of
    at least one - across the whole of its range, not merely at its start.

    Periods are counts of bars, both in a condition and in a ``search`` range.
    ``$placeholder`` periods are NOT resolved here - the returned definition
    still carries them in :attr:`IndicatorRef.period_param`, for the grid to
    substitute.

    Args:
        path: Path to the config file, relative to the working directory.

    Returns:
        The parsed and validated strategy definition.

    Raises:
        StrategyConfigError: On any failure, naming the offending field - or the
            file itself, for a failure that is the file's rather than a field's.
            No other exception type escapes.
    """
    source = path.name
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise _fail(source, "", f"file not found at '{path}'") from exc
    except UnicodeDecodeError as exc:
        raise _fail(source, "", f"file at '{path}' is not valid UTF-8") from exc
    except OSError as exc:
        raise _fail(source, "", f"cannot read '{path}': {exc}") from exc
    except ValueError as exc:
        # A path carrying an embedded null byte fails in os.open as ValueError,
        # which is not an OSError and would otherwise escape the contract above.
        raise _fail(source, "", f"cannot read '{path}': {exc}") from exc

    try:
        # _NoDuplicateKeyLoader is a SafeLoader subclass: no arbitrary construction.
        parsed = yaml.load(text, Loader=_NoDuplicateKeyLoader)
    except yaml.YAMLError as exc:
        raise _fail(source, "", f"not valid YAML: {exc}") from exc
    except RecursionError as exc:
        # PyYAML's composer recurses once per level of nesting, so a file nested
        # past the interpreter's limit raises RecursionError - a RuntimeError,
        # not a YAMLError, so the handler above does not see it. This format has
        # no nesting at all, which makes such a file a rejection rather than a
        # crash.
        raise _fail(
            source, "", "nests too deeply to parse; this format has no nested structures"
        ) from exc

    if parsed is None:
        raise _fail(source, "", f"file at '{path}' is empty")
    root = _as_mapping(parsed, source, "<root>")
    _reject_unknown_keys(root, _TOP_LEVEL_KEYS, source, "<root>")

    version = _as_int(_require(root, "version", source, ""), source, "version")
    if version != SUPPORTED_VERSION:
        raise _fail(
            source,
            "version",
            f"is {version}, and this loader understands only version {SUPPORTED_VERSION}",
        )

    description = _as_str(_require(root, "description", source, ""), source, "description")
    if not description.strip():
        raise _fail(
            source,
            "description",
            "must state the strategy and the mechanism it claims to exploit, and is empty",
        )

    definition = StrategyDef(
        name=_as_name(_require(root, "name", source, ""), source, "name"),
        version=version,
        description=description.strip(),
        entry=_conditions(_require(root, "entry", source, ""), source, "entry"),
        exit=_conditions(_require(root, "exit", source, ""), source, "exit"),
        search=_search(root.get("search", {}), source),
        fixed=_fixed(root.get("fixed", {}), source),
    )
    _check_parameters(definition, source)
    return definition
