"""Artifact values and the collection used to move outputs between stages."""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from string import hexdigits
from typing import Iterable, Iterator, Mapping

from omniship.core.execution import Architecture, ExecutionHost, OperatingSystem
from omniship.core.stage import Stage

ANY_PLATFORM = "any"


def _platform_tag(value: object) -> str | None:
    """Normalize an explicit artifact platform to ``any`` or ``<os>/<arch>``."""

    if value is None or value == ANY_PLATFORM:
        return value
    host = value
    if isinstance(value, str):
        system, separator, architecture = value.partition("/")
        try:
            host = ExecutionHost(OperatingSystem(system), Architecture(architecture))
        except ValueError:
            host = None
        if not separator:
            host = None
    if (
        not isinstance(host, ExecutionHost)
        or host.os is OperatingSystem.UNKNOWN
        or host.architecture is Architecture.UNKNOWN
    ):
        known_systems = ", ".join(
            item.value
            for item in OperatingSystem
            if item is not OperatingSystem.UNKNOWN
        )
        known_architectures = ", ".join(
            item.value for item in Architecture if item is not Architecture.UNKNOWN
        )
        raise ValueError(
            f"Artifact platform must be '{ANY_PLATFORM}' or '<os>/<architecture>' "
            f"with os in {known_systems} and architecture in {known_architectures}; "
            f"got {value!r}"
        )
    return f"{host.os.value}/{host.architecture.value}"


@dataclass(frozen=True, slots=True)
class ArtifactDigest:
    """A content digest recorded when an artifact is bundled."""

    algorithm: str
    value: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise ValueError("Unsupported artifact digest algorithm")
        if len(self.value) != 64 or any(char not in hexdigits for char in self.value):
            raise ValueError("Artifact SHA-256 digest must contain 64 hex characters")
        object.__setattr__(self, "value", self.value.lower())


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    """Execution identity associated with an artifact producer."""

    stage: Stage | None = None
    task: str | None = None
    revision: str | None = None
    host: ExecutionHost | None = None


@dataclass(frozen=True, slots=True)
class Artifact:
    """A named file or directory produced by a pipeline operation.

    ``platform`` is an explicit platform tag: ``any`` for platform-independent
    files, or ``<os>/<architecture>`` such as ``windows/x86_64`` for files
    built for a platform other than the producing host (cross-compiled
    output). ``None`` means no explicit tag; the producing host is recorded in
    ``provenance``.
    """

    path: Path
    name: str
    metadata: Mapping[str, str] = field(default_factory=dict)
    digest: ArtifactDigest | None = None
    provenance: ArtifactProvenance | None = None
    platform: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "platform", _platform_tag(self.platform))

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        name: str | None = None,
        metadata: Mapping[str, str] | None = None,
        digest: ArtifactDigest | None = None,
        provenance: ArtifactProvenance | None = None,
        platform: str | ExecutionHost | None = None,
    ) -> "Artifact":
        """Create an artifact with an absolute path and copied metadata."""

        p = Path(path).resolve()
        return cls(
            path=p,
            name=name or p.name,
            metadata=dict(metadata or {}),
            digest=digest,
            provenance=provenance,
            platform=platform,
        )

    def matches(self, reference: str) -> bool:
        """Match by name, full path, or trailing whole path segments."""

        if self.name == reference or str(self.path) == reference:
            return True
        parts = Path(reference).parts
        return bool(parts) and self.path.parts[-len(parts) :] == parts


@dataclass
class ArtifactSet:
    """An insertion-ordered collection deduplicated by artifact path."""

    _artifacts: list[Artifact] = field(default_factory=list)

    def add(self, artifact: Artifact) -> None:
        """Add an artifact unless another artifact already uses its path."""

        # Avoid duplicate identical paths
        if not any(a.path == artifact.path for a in self._artifacts):
            self._artifacts.append(artifact)

    def get(self, name_or_path: str) -> Artifact | None:
        """Find the first artifact by name, full path, or path suffix."""

        return next((a for a in self._artifacts if a.matches(name_or_path)), None)

    def __iter__(self) -> Iterator[Artifact]:
        return iter(self._artifacts)

    def __len__(self) -> int:
        return len(self._artifacts)

    def __contains__(self, name_or_path: str) -> bool:
        return self.get(name_or_path) is not None

    def to_list(self) -> list[Artifact]:
        """Return a shallow copy suitable for operation inputs."""

        return list(self._artifacts)

    def require(
        self,
        names: Iterable[str],
        *,
        exact: bool = False,
        revision: str | None = None,
        consistent_revision: bool = False,
    ) -> tuple[Artifact, ...]:
        """Return named artifacts after validating the requested contract."""

        requested = tuple(names)
        counts = Counter(artifact.name for artifact in self._artifacts)
        duplicates = [name for name in requested if counts[name] > 1]
        if duplicates:
            raise ValueError(
                f"Duplicate artifacts: {', '.join(dict.fromkeys(duplicates))}"
            )
        selected = tuple(
            artifact for name in requested if (artifact := self.get(name)) is not None
        )
        selected_names = {artifact.name for artifact in selected}
        missing = [name for name in requested if name not in selected_names]
        if missing:
            raise ValueError(f"Missing required artifacts: {', '.join(missing)}")
        if exact:
            requested_names = set(requested)
            unexpected = [
                artifact.name
                for artifact in self._artifacts
                if artifact.name not in requested_names
            ]
            if unexpected:
                raise ValueError(f"Unexpected artifacts: {', '.join(unexpected)}")
        revisions = {
            artifact.provenance.revision if artifact.provenance is not None else None
            for artifact in selected
        }
        if consistent_revision and len(revisions) > 1:
            raise ValueError("Artifacts have mixed producing revisions")
        if revision is not None and revisions != {revision}:
            raise ValueError(
                f"Artifacts were not produced by expected revision '{revision}'"
            )
        return selected
