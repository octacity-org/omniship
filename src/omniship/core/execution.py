"""Provider-neutral descriptions of execution hosts and job resources."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping

REVISION_ENV = "OMNISHIP_REVISION"
REF_TYPE_ENV = "OMNISHIP_REF_TYPE"
REF_NAME_ENV = "OMNISHIP_REF_NAME"


def execution_placements(execution: Any) -> tuple[Any, ...]:
    """Return the per-target placements declared by a task's ``execution``.

    ``execution`` is either one placement or a sequence of placements. Each
    placement names the workflow target that owns it in its ``target``
    attribute, so one task can carry placement for several CI providers.
    """

    if execution is None:
        return ()
    if isinstance(execution, (list, tuple)):
        return tuple(execution)
    return (execution,)


def placement_for(execution: Any, target: str) -> Any | None:
    """Return the placement in ``execution`` owned by ``target``, if any."""

    for placement in execution_placements(execution):
        if getattr(placement, "target", None) == target:
            return placement
    return None


@dataclass(frozen=True, slots=True)
class IdentityToken:
    """A runtime identity token required for trusted publishing."""

    name: str = "core/identity-token"


class OperatingSystem(StrEnum):
    """Operating system family of the machine executing a task."""

    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class Architecture(StrEnum):
    """Provider-neutral CPU architecture of an execution host."""

    X86_64 = "x86_64"
    ARM64 = "arm64"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExecutionHost:
    """The machine running a task, distinct from a compiler's output target."""

    os: OperatingSystem
    architecture: Architecture

    @classmethod
    def detect(cls) -> ExecutionHost:
        """Describe the current process host using normalized platform names."""

        return cls.from_platform(platform.system(), platform.machine())

    @classmethod
    def from_platform(cls, system: str, machine: str) -> ExecutionHost:
        """Normalize values reported by :mod:`platform` into core enums."""

        normalized_system = system.strip().lower()
        normalized_machine = machine.strip().lower()
        operating_system = {
            "linux": OperatingSystem.LINUX,
            "darwin": OperatingSystem.MACOS,
            "windows": OperatingSystem.WINDOWS,
        }.get(normalized_system, OperatingSystem.UNKNOWN)
        architecture = {
            "amd64": Architecture.X86_64,
            "x86_64": Architecture.X86_64,
            "aarch64": Architecture.ARM64,
            "arm64": Architecture.ARM64,
        }.get(normalized_machine, Architecture.UNKNOWN)
        return cls(operating_system, architecture)


_SECRET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class SecretRef:
    """A reference to a provider-managed secret, never the secret value itself."""

    name: str

    def __post_init__(self) -> None:
        if not _SECRET_NAME.fullmatch(self.name):
            raise ValueError("secret name must contain only letters, digits, and '_'")


@dataclass(frozen=True, slots=True)
class CacheSpec:
    """Provider-neutral dependency-cache declaration."""

    paths: tuple[str, ...]
    key: str
    restore_keys: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        paths: Iterable[str],
        key: str,
        restore_keys: Iterable[str] = (),
    ) -> None:
        if isinstance(paths, (str, bytes)):
            raise TypeError("cache paths must be an iterable of strings")
        if isinstance(restore_keys, (str, bytes)):
            raise TypeError("cache restore_keys must be an iterable of strings")
        normalized_paths = tuple(paths)
        normalized_restore_keys = tuple(restore_keys)
        if not normalized_paths or any(
            not isinstance(path, str) or not path for path in normalized_paths
        ):
            raise ValueError("cache requires at least one non-empty path")
        if not isinstance(key, str) or not key:
            raise ValueError("cache key cannot be empty")
        if any(
            not isinstance(restore_key, str) or not restore_key
            for restore_key in normalized_restore_keys
        ):
            raise ValueError("cache restore keys cannot be empty")
        object.__setattr__(self, "paths", normalized_paths)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "restore_keys", normalized_restore_keys)


def load_execution_revision(env: Mapping[str, str]) -> str | None:
    """Read the source revision supplied by an execution target, if present."""

    revision = env.get(REVISION_ENV)
    return revision or None
