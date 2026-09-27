"""Publish existing release archives through an installed Homebrew tap."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omniship.core.stage import Stage
from omniship.plugins.metadata import OperationDefinition
from omniship.plugins.registry import PluginRegistry
from omniship.plugins.tooling import FacadeOperation, ToolFacade
from omniship.runtime import TaskFailure
from omniship.workflow.errors import WorkflowError
from omniship.workflow.model import NodeSpec


class HomebrewMode(StrEnum):
    PULL_REQUEST = "pull-request"
    DIRECT = "direct"
    WRITE_ONLY = "write-only"


@dataclass(frozen=True)
class HomebrewTap:
    """Install a tap on a brew-enabled runner; authentication comes from CI."""

    tap: str

    def __post_init__(self) -> None:
        if (
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_-]*", self.tap
            )
            is None
        ):
            raise ValueError("Homebrew tap must use owner/tap syntax")

    @property
    def name(self) -> str:
        return f"homebrew/tap:{self.tap}"

    def github(self) -> dict[str, str]:
        return {
            "name": f"Set up Homebrew tap {self.tap}",
            "run": f"brew tap {self.tap}",
        }


class _UpdateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: str = Field(min_length=1)
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._,+-]*$")
    url: str
    checksum_artifact: str | None = None
    mode: HomebrewMode = HomebrewMode.PULL_REQUEST
    branch: str | None = None
    dry_run: bool = False

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or re.search(r'[\s"\\]', value)
        ):
            raise ValueError(
                "Homebrew URL must be public HTTPS without credentials, query or fragment"
            )
        return value

    @model_validator(mode="after")
    def direct_branch(self):
        if self.mode == HomebrewMode.DIRECT:
            if (
                not self.branch
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", self.branch)
                or any(part in self.branch for part in ("..", "//", "@{"))
                or self.branch.endswith(("/", ".", ".lock"))
            ):
                raise ValueError(
                    "Direct Homebrew publishing requires an explicit valid branch"
                )
        elif self.branch is not None:
            raise ValueError("branch is only used with direct publishing")
        return self


_TAP_ITEM = r"^[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9@+_.-]*$"


class _FormulaConfig(_UpdateConfig):
    formula: str = Field(pattern=_TAP_ITEM)


class _CaskConfig(_UpdateConfig):
    cask: str = Field(pattern=_TAP_ITEM)


class Homebrew(ToolFacade):
    """Update formulae/casks using brew, with no embedded repository credentials.

    The tap must already be installed and authenticated by the CI provider. PRs
    use brew's existing GitHub integration. Direct mode requires a clean tap on
    the explicitly selected branch and performs a normal, non-force push.
    """

    def formula(
        self,
        *,
        formula: str,
        artifact: str,
        version: str,
        url: str,
        checksum_artifact: str | None = None,
        mode: HomebrewMode = HomebrewMode.PULL_REQUEST,
        branch: str | None = None,
        dry_run: bool = False,
    ) -> None:
        cfg = _FormulaConfig(
            formula=formula,
            artifact=artifact,
            version=version,
            url=url,
            checksum_artifact=checksum_artifact,
            mode=mode,
            branch=branch,
            dry_run=dry_run,
        )
        self._update("formula", cfg.formula, cfg)

    def cask(
        self,
        *,
        cask: str,
        artifact: str,
        version: str,
        url: str,
        checksum_artifact: str | None = None,
        mode: HomebrewMode = HomebrewMode.PULL_REQUEST,
        branch: str | None = None,
        dry_run: bool = False,
    ) -> None:
        cfg = _CaskConfig(
            cask=cask,
            artifact=artifact,
            version=version,
            url=url,
            checksum_artifact=checksum_artifact,
            mode=mode,
            branch=branch,
            dry_run=dry_run,
        )
        self._update("cask", cfg.cask, cfg)

    def _artifact_file(self, name: str) -> Path:
        matches = [item for item in self.context.artifacts if item.name == name]
        if len(matches) != 1:
            raise TaskFailure(f"Expected exactly one release artifact named {name}")
        path = matches[0].path
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(self.context.workspace)
        ):
            raise TaskFailure(f"Invalid release artifact: {name}")
        return path

    def _checksum(self, cfg: _UpdateConfig) -> str:
        archive = self._artifact_file(cfg.artifact)
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if cfg.checksum_artifact:
            manifest = self._artifact_file(cfg.checksum_artifact)
            matches = []
            for line in manifest.read_text(encoding="utf-8").splitlines():
                match = re.fullmatch(r"([A-Fa-f0-9]{64})\s+\*?(.+)", line)
                if match and match[2] in {archive.name, cfg.artifact}:
                    matches.append(match[1].lower())
            if matches != [digest]:
                raise TaskFailure(
                    "Release checksum manifest is missing, ambiguous or does not match the archive"
                )
        return digest

    def _update(self, kind: str, item: str, cfg: _UpdateConfig) -> None:
        digest = self._checksum(cfg)
        if cfg.mode == HomebrewMode.PULL_REQUEST:
            self._require_env("HOMEBREW_GITHUB_API_TOKEN", dry_run=cfg.dry_run)
        args = [
            "brew",
            f"bump-{kind}-pr",
            item,
            f"--version={cfg.version}",
            f"--url={cfg.url}",
            f"--sha256={digest}",
            "--no-browse",
        ]
        tap = None
        if cfg.dry_run:
            # No brew invocation: some developer commands may prepare git state
            # or download files even when passed --dry-run.
            self.context.log.info(
                f"Dry run: update {item} to {cfg.version} ({cfg.mode}); SHA256 {digest}"
            )
            return
        if cfg.mode == HomebrewMode.PULL_REQUEST:
            args.append("--no-fork")
        else:
            args.append("--write-only")
            if cfg.mode == HomebrewMode.DIRECT:
                tap_name = "/".join(item.split("/")[:2])
                tap = Path(
                    self._run(["brew", "--repository", tap_name]).strip()
                ).resolve()
                if self._run(
                    ["git", "-C", str(tap), "rev-parse", "--show-toplevel"]
                ).strip() != str(tap):
                    raise TaskFailure("Homebrew tap is not a Git repository root")
                if self._run(["git", "-C", str(tap), "status", "--porcelain"]).strip():
                    raise TaskFailure("Direct Homebrew publishing requires a clean tap")
                if (
                    self._run(
                        ["git", "-C", str(tap), "branch", "--show-current"]
                    ).strip()
                    != cfg.branch
                ):
                    raise TaskFailure("Homebrew tap is not on the configured branch")
                head = self._run(["git", "-C", str(tap), "rev-parse", "HEAD"]).strip()
                remote = self._run(
                    [
                        "git",
                        "-C",
                        str(tap),
                        "ls-remote",
                        "--exit-code",
                        "origin",
                        f"refs/heads/{cfg.branch}",
                    ]
                ).split()
                if not remote or remote[0] != head:
                    raise TaskFailure(
                        "Homebrew tap HEAD must match the remote branch before updating"
                    )
                args.append("--commit")
        self._run(args, env={"HOMEBREW_NO_AUTO_UPDATE": "1"})
        if tap is not None:
            self._run(
                [
                    "git",
                    "-C",
                    str(tap),
                    "push",
                    "origin",
                    f"HEAD:refs/heads/{cfg.branch}",
                ]
            )


@dataclass(frozen=True, kw_only=True)
class HomebrewFormula:
    formula: str
    artifact: str
    version: str
    url: str
    checksum_artifact: str | None = None
    mode: HomebrewMode = HomebrewMode.PULL_REQUEST
    branch: str | None = None
    dry_run: bool = False
    name: str = "homebrew-formula"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        return _compile(self, stage, "homebrew/formula", _FormulaConfig)


@dataclass(frozen=True, kw_only=True)
class HomebrewCask:
    cask: str
    artifact: str
    version: str
    url: str
    checksum_artifact: str | None = None
    mode: HomebrewMode = HomebrewMode.PULL_REQUEST
    branch: str | None = None
    dry_run: bool = False
    name: str = "homebrew-cask"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        return _compile(self, stage, "homebrew/cask", _CaskConfig)


def _compile(block, stage, operation, model) -> list[NodeSpec]:
    if stage != Stage.SHIP:
        raise WorkflowError(
            f"{type(block).__name__} can only be used in the ship stage"
        )
    params = asdict(block)
    params.pop("name")
    cfg = model.model_validate(params)
    return [NodeSpec(block.name, stage, operation, cfg.model_dump(mode="json"))]


def register_homebrew_plugin(registry: PluginRegistry) -> None:
    for name, model, method in (
        ("homebrew/formula", _FormulaConfig, "formula"),
        ("homebrew/cask", _CaskConfig, "cask"),
    ):
        operation = FacadeOperation(
            name,
            Stage.SHIP,
            model,
            lambda ctx, cfg, method=method: getattr(Homebrew(ctx), method)(
                **cfg.model_dump()
            ),
        )
        registry.register_operation(
            operation,
            OperationDefinition(
                name=name,
                stages=operation.stages,
                description=f"Publish {name} from a verified release artifact",
                config_model=model,
            ),
        )
    registry.register_requirement_resolver(
        "github/actions", HomebrewTap, lambda requirement: requirement.github()
    )


__all__ = [
    "Homebrew",
    "HomebrewFormula",
    "HomebrewCask",
    "HomebrewMode",
    "HomebrewTap",
    "register_homebrew_plugin",
]
