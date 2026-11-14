"""Raw YAML escape hatches for generated CI files.

Overrides change the generated document after OmniShip builds it, for
settings OmniShip does not model yet. They bypass OmniShip's validation.
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Override:
    """Set, add or (with ``None``) remove the value at one path."""

    path: tuple[str | int, ...]
    value: object

    @property
    def display(self) -> str:
        return ".".join(str(segment) for segment in self.path)


def normalize_overrides(overrides: object) -> tuple[Override, ...]:
    """Validate ``{path: value}`` overrides.

    A path is a dotted string, or a tuple of keys and list indexes when a key
    contains a dot.
    """

    if not isinstance(overrides, Mapping):
        raise TypeError("overrides must map paths to values")
    normalized = []
    for path, value in overrides.items():
        if isinstance(path, str):
            segments: tuple[str | int, ...] = tuple(path.split("."))
        elif isinstance(path, tuple):
            segments = path
        else:
            raise TypeError(f"override path {path!r} must be a string or tuple")
        if not segments or any(
            isinstance(segment, bool)
            or not isinstance(segment, (str, int))
            or segment == ""
            for segment in segments
        ):
            raise ValueError(f"override path {path!r} has an empty or invalid part")
        normalized.append(Override(segments, value))
    return tuple(normalized)


def normalize_extra_jobs(extra_jobs: object) -> dict[str, dict[str, object]]:
    """Validate ``{job id: raw job}`` extra jobs."""

    if not isinstance(extra_jobs, Mapping) or any(
        not isinstance(name, str) or not name or not isinstance(job, Mapping)
        for name, job in extra_jobs.items()
    ):
        raise TypeError("extra_jobs must map job names to job mappings")
    return {name: dict(job) for name, job in extra_jobs.items()}


def add_extra_jobs(
    jobs: dict[str, object],
    extra_jobs: Mapping[str, Mapping[str, object]],
    where: str,
) -> None:
    for name, job in extra_jobs.items():
        if name in jobs:
            raise ValueError(
                f"{where} extra job '{name}' has the same name as a generated "
                "job or key"
            )
        jobs[name] = copy.deepcopy(dict(job))


def apply_overrides(
    document: dict[str, object],
    overrides: tuple[Override, ...],
    where: str,
) -> dict[str, object]:
    """Return a copy of ``document`` with ``overrides`` applied in order."""

    if not overrides:
        return document
    # Generators may share objects between documents; edit a private copy.
    document = copy.deepcopy(document)
    for override in overrides:
        try:
            _apply(document, override)
        except _PathError as error:
            raise ValueError(
                f"{where} override '{override.display}': {error}"
            ) from None
    return document


class _PathError(Exception):
    pass


def _apply(document: dict[str, object], override: Override) -> None:
    container: object = document
    *parents, last = override.path
    previous: str | int | None = None
    for segment in parents:
        if not isinstance(container, (dict, list)):
            raise _PathError(f"'{previous}' is not a mapping or list")
        container = _child(container, segment)
        previous = segment
    if isinstance(container, dict):
        key = str(last)
        if override.value is None:
            if key not in container:
                raise _PathError(f"'{key}' does not exist")
            del container[key]
        else:
            container[key] = copy.deepcopy(override.value)
    elif isinstance(container, list):
        if last == "-":
            if override.value is None:
                raise _PathError("'-' appends a value and cannot remove one")
            container.append(copy.deepcopy(override.value))
            return
        index = _index(container, last)
        if override.value is None:
            del container[index]
        else:
            container[index] = copy.deepcopy(override.value)
    else:
        raise _PathError(f"'{previous}' is not a mapping or list")


def _child(container: dict | list, segment: str | int) -> object:
    if isinstance(container, dict):
        key = str(segment)
        if key not in container:
            raise _PathError(f"'{key}' does not exist")
        return container[key]
    return container[_index(container, segment)]


def _index(items: list, segment: str | int) -> int:
    """A list item by index, or by its ``name`` (GitHub steps)."""

    if isinstance(segment, int) or segment.isdigit():
        index = int(segment)
        if not 0 <= index < len(items):
            raise _PathError(f"index {index} is out of range")
        return index
    matches = [
        position
        for position, item in enumerate(items)
        if isinstance(item, dict) and item.get("name") == segment
    ]
    if not matches:
        raise _PathError(f"no item named '{segment}'")
    if len(matches) > 1:
        raise _PathError(f"{len(matches)} items are named '{segment}'")
    return matches[0]
