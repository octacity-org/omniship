import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from omniship.core.execution import SecretRef
from omniship.core.input import runtime_input_reference
from omniship.core.stage import Stage
from omniship.plugins.github.actions import (
    GitHubBooleanInput,
    GitHubPermissions,
    GitHubStringInput,
)
from omniship.workflow.errors import WorkflowError
from omniship.workflow.model import NodeSpec


@dataclass(frozen=True)
class GitHubExternalWorkflow:
    """Call an external reusable workflow as one opaque stage task."""

    workflow: str
    ref: str
    inputs: Mapping[str, str | int | float | bool] = field(default_factory=dict)
    secrets: Mapping[str, SecretRef] = field(default_factory=dict)
    permissions: GitHubPermissions | None = None
    name: str = "external-workflow"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workflow, str)
            or re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.ya?ml",
                self.workflow,
            )
            is None
        ):
            raise ValueError(
                "External workflow must be 'owner/repo/workflow.yml' or '.yaml'"
            )
        if (
            not isinstance(self.ref, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", self.ref) is None
        ):
            raise ValueError(
                "External workflow ref must be a non-empty Git ref or commit SHA"
            )
        inputs = dict(self.inputs)
        secrets = dict(self.secrets)
        if any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None
            or not isinstance(value, (str, int, float, bool))
            for key, value in inputs.items()
        ):
            raise ValueError("External workflow inputs must be named scalar values")
        if any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None
            or not isinstance(value, SecretRef)
            for key, value in secrets.items()
        ):
            raise ValueError("External workflow secrets must be named SecretRef values")
        if self.permissions is not None and not isinstance(
            self.permissions, GitHubPermissions
        ):
            raise TypeError("External workflow permissions must be GitHubPermissions")
        object.__setattr__(self, "inputs", MappingProxyType(inputs))
        object.__setattr__(self, "secrets", MappingProxyType(secrets))

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        owner, repository, filename = self.workflow.split("/")
        params: dict[str, object] = {
            "uses": f"{owner}/{repository}/.github/workflows/{filename}@{self.ref}",
        }
        if self.inputs:
            params["inputs"] = dict(self.inputs)
        if self.secrets:
            params["secrets"] = {
                name: secret.name for name, secret in self.secrets.items()
            }
        if self.permissions is not None:
            params["permissions"] = self.permissions.to_document()
        return [NodeSpec(self.name, stage, "github/external-workflow", params)]


def _project_version(workspace_root: Path) -> str:
    path = workspace_root / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        version = data["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise WorkflowError(
            "Could not resolve [project].version from pyproject.toml"
        ) from exc
    if not isinstance(version, str) or not version:
        raise WorkflowError("Project version must be a non-empty string")
    return version


def _github_repository(workspace_root: Path) -> str:
    process = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise WorkflowError("Could not resolve GitHub repository from origin remote")
    url = process.stdout.strip()
    if url.startswith("git@github.com:"):
        repository = url.removeprefix("git@github.com:")
    elif url.startswith("https://github.com/"):
        repository = url.removeprefix("https://github.com/")
    else:
        raise WorkflowError("Origin is not a supported GitHub remote")
    return repository.removesuffix(".git")


@dataclass(frozen=True)
class GitHubRelease:
    repository: str | None = None
    tag: str | GitHubStringInput | None = None
    notes: str | None = "auto"
    title: str | None = None
    draft: bool = False
    prerelease: bool | GitHubBooleanInput = False
    files: tuple[str, ...] = ()
    dry_run: bool = False
    name: str = "github-release"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.SHIP:
            raise WorkflowError("GitHubRelease can only be used in the ship stage")
        tag: object
        if isinstance(self.tag, GitHubStringInput):
            tag = runtime_input_reference(self.tag.name)
        else:
            tag = self.tag or f"v{_project_version(workspace_root)}"
        params: dict[str, object] = {
            "repository": self.repository or _github_repository(workspace_root),
            "tag": tag,
        }
        if self.notes == "auto":
            params["generate_notes"] = True
        elif self.notes:
            params["body"] = self.notes
        if self.title:
            params["title"] = self.title
        if self.draft:
            params["draft"] = True
        if isinstance(self.prerelease, GitHubBooleanInput):
            params["prerelease"] = runtime_input_reference(self.prerelease.name)
        elif self.prerelease:
            params["prerelease"] = True
        if self.files:
            params["files"] = list(self.files)
        if self.dry_run:
            params["dry_run"] = True
        return [NodeSpec(self.name, stage, "github/release", params)]


@dataclass(frozen=True)
class GitHubTag:
    repository: str | None = None
    tag: str | GitHubStringInput | None = None
    target: str | None = None
    force: bool = False
    dry_run: bool = False
    name: str = "github-tag"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.SHIP:
            raise WorkflowError("GitHubTag can only be used in the ship stage")
        tag: object
        if isinstance(self.tag, GitHubStringInput):
            tag = runtime_input_reference(self.tag.name)
        else:
            tag = self.tag or f"v{_project_version(workspace_root)}"
        params: dict[str, object] = {
            "repository": self.repository or _github_repository(workspace_root),
            "tag": tag,
        }
        if self.target:
            params["target"] = self.target
        if self.force:
            params["force"] = True
        if self.dry_run:
            params["dry_run"] = True
        return [NodeSpec(self.name, stage, "github/tag", params)]


@dataclass(frozen=True)
class GitHubPages:
    artifact: str = "site"
    name: str = "github-pages"

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, str) or not self.artifact:
            raise TypeError("GitHub Pages artifact must be a non-empty string")

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.SHIP:
            raise WorkflowError("GitHubPages can only be used in the ship stage")
        return [
            NodeSpec(
                self.name,
                stage,
                "github/pages",
                {"artifact": self.artifact},
            )
        ]
