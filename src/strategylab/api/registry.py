"""Which strategies the project holds: the config files in ``configs/``.

The registry is the directory. A strategy is a ``.yaml`` file there that the
project's own loader accepts, and its id is the file's name without the
extension. Nothing is copied and nothing is stored beside the file, so there
is no second record that can disagree with it.

EVERY STRATEGY IS A DRAFT, because nothing in this project can deploy one yet.
When deploying exists, the state it sets will need a file of its own; until
then a state file would record a thing that cannot change.

AN ID FROM A REQUEST IS LOOKED UP, NEVER OPENED. :func:`find_strategy` compares
the id against the ids of the files that are there. It builds no path from it,
so an id written to climb out of ``configs/`` matches nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from strategylab.api.reading import plain_reason
from strategylab.core.config import StrategyConfigError, config_content_hash, load_strategy

#: Where strategy configs live, relative to the project root.
CONFIGS_DIR = Path("configs")

#: The one file in ``configs/`` that is not a strategy.
LIMITS_FILE = CONFIGS_DIR / "risk_limits.yaml"

#: Largest strategy config read, in bytes. The committed one is a few
#: kilobytes; a megabyte of YAML in ``configs/`` is not a strategy.
MAX_CONFIG_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StrategyFile:
    """One strategy config that loaded.

    Attributes:
        id: The file's name without its extension. What the app's requests use.
        name: The name the config declares. What search artifacts record.
        description: The config's description, on one line.
        config: The file's path relative to the project root, with forward
            slashes.
        config_hash: Content hash of the config as it is now.
    """

    id: str
    name: str
    description: str
    config: str
    config_hash: str


def list_strategies(root: Path) -> tuple[list[StrategyFile], list[dict[str, str]]]:
    """List the strategy configs in ``configs/``, and the ones that do not load.

    Every ``.yaml`` file there except the risk limits file is taken for a
    strategy. One the loader refuses, or one larger than
    :data:`MAX_CONFIG_BYTES`, is reported with the reason, not dropped: a draft
    written from a video is exactly the file most likely to be wrong, and
    silence about it would read as "no such strategy". So is one whose name or
    description holds text that could not be sent to the window.

    Args:
        root: The project root.

    Returns:
        The strategies that loaded, in file-name order, and the files that did
        not as ``{"config": ..., "reason": ...}``.
    """
    loaded: list[StrategyFile] = []
    refused: list[dict[str, str]] = []
    for path in sorted((root / CONFIGS_DIR).glob("*.yaml")):
        relative = path.relative_to(root)
        if relative == LIMITS_FILE:
            continue
        try:
            size = path.stat().st_size
            if size > MAX_CONFIG_BYTES:
                raise StrategyConfigError(
                    f"the file is {size} bytes, more than the {MAX_CONFIG_BYTES} a strategy "
                    "config is read up to"
                )
            definition = load_strategy(path)
            config_hash = config_content_hash(path)
            for text in (definition.name, definition.description):
                text.encode("utf-8")
        except UnicodeEncodeError:
            refused.append(
                {
                    "config": relative.as_posix(),
                    "reason": "its name or description holds text that is not valid Unicode",
                }
            )
            continue
        except (StrategyConfigError, OSError) as exc:
            refused.append({"config": relative.as_posix(), "reason": plain_reason(root, exc)})
            continue
        loaded.append(
            StrategyFile(
                id=path.stem,
                name=definition.name,
                description=" ".join(definition.description.split()),
                config=relative.as_posix(),
                config_hash=config_hash,
            )
        )
    return loaded, refused


def find_strategy(root: Path, strategy_id: str) -> StrategyFile | None:
    """Find a strategy by the id a request gave.

    Args:
        root: The project root.
        strategy_id: The id, as the app was given it by :func:`list_strategies`.

    Returns:
        The strategy whose id is exactly ``strategy_id``, or ``None``.
    """
    loaded, _ = list_strategies(root)
    for strategy in loaded:
        if strategy.id == strategy_id:
            return strategy
    return None
