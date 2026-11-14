from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from omniship.core.input import ref_template
from omniship.core.stage import Stage
from omniship.workflow.errors import WorkflowError
from omniship.workflow.model import NodeSpec


def _base_name(output: str, suffix: str) -> str:
    # Braces of {tag} or {version} cannot appear in node names.
    name = Path(output).name.replace("{", "").replace("}", "")
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    return name or "archive"


def _archive_params(output: str, files: Mapping[str, str], reproducible: bool) -> dict:
    """{tag} and {version} are filled in from the pushed tag when it runs."""

    return {
        "output": ref_template(output),
        "files": ref_template(dict(files)),
        "reproducible": reproducible,
    }


@dataclass(frozen=True)
class TarGz:
    output: str
    files: Mapping[str, str]
    reproducible: bool = True
    name: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            object.__setattr__(
                self,
                "name",
                f"{_base_name(self.output, '.tar.gz')}-tar-gz",
            )

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.BUILD:
            raise WorkflowError("TarGz can only be used in the build stage")
        return [NodeSpec(self.name, stage, "packaging/tar-gz", self._params())]

    def _params(self) -> dict[str, object]:
        return _archive_params(self.output, self.files, self.reproducible)


@dataclass(frozen=True)
class Zip:
    output: str
    files: Mapping[str, str]
    reproducible: bool = True
    name: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            object.__setattr__(
                self,
                "name",
                f"{_base_name(self.output, '.zip')}-zip",
            )

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.BUILD:
            raise WorkflowError("Zip can only be used in the build stage")
        return [
            NodeSpec(
                self.name,
                stage,
                "packaging/zip",
                _archive_params(self.output, self.files, self.reproducible),
            )
        ]


@dataclass(frozen=True)
class Sha256Manifest:
    output: str = "dist/SHA256SUMS.txt"
    artifacts: tuple[str, ...] = ()
    name: str = "sha256-manifest"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.BUILD:
            raise WorkflowError("Sha256Manifest can only be used in the build stage")
        params: dict[str, object] = {"output": self.output}
        if self.artifacts:
            params["artifacts"] = list(self.artifacts)
        return [NodeSpec(self.name, stage, "packaging/sha256-manifest", params)]
