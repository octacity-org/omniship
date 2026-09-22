"""Official Go workflow primitives."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from omniship.core.execution import Architecture, ExecutionHost, OperatingSystem
from omniship.core.stage import Stage
from omniship.plugins.github import GitHubActionStep
from omniship.plugins.metadata import OperationDefinition
from omniship.plugins.registry import PluginRegistry
from omniship.plugins.tooling import (
    FacadeOperation,
    ToolFacade,
    exact_version,
    relative_file,
)
from omniship.runtime import TaskFailure
from omniship.workflow.errors import WorkflowError
from omniship.workflow.model import NodeSpec


class GoOS(StrEnum):
    AIX = "aix"
    ANDROID = "android"
    DARWIN = "darwin"
    DRAGONFLY = "dragonfly"
    FREEBSD = "freebsd"
    ILLUMOS = "illumos"
    IOS = "ios"
    JS = "js"
    LINUX = "linux"
    NETBSD = "netbsd"
    OPENBSD = "openbsd"
    PLAN9 = "plan9"
    SOLARIS = "solaris"
    WASIP1 = "wasip1"
    WINDOWS = "windows"


class GoArch(StrEnum):
    X86 = "386"
    AMD64 = "amd64"
    ARM = "arm"
    ARM64 = "arm64"
    LOONG64 = "loong64"
    MIPS = "mips"
    MIPS64 = "mips64"
    MIPS64LE = "mips64le"
    MIPSLE = "mipsle"
    PPC64 = "ppc64"
    PPC64LE = "ppc64le"
    RISCV64 = "riscv64"
    S390X = "s390x"
    WASM = "wasm"


@dataclass(frozen=True, slots=True)
class GoTarget:
    os: GoOS
    arch: GoArch

    def __post_init__(self) -> None:
        if not isinstance(self.os, GoOS) or not isinstance(self.arch, GoArch):
            raise TypeError("GoTarget requires GoOS and GoArch values")

    @property
    def environment(self) -> dict[str, str]:
        return {"GOOS": self.os.value, "GOARCH": self.arch.value}


@dataclass(frozen=True, slots=True)
class GoModule:
    """A Go module whose Git tag is prefixed by its repository path."""

    path: str = "."

    def __post_init__(self) -> None:
        relative_file(self.path, field_name="Go module path")

    def tag(self, version: str) -> str:
        if re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version) is None:
            raise ValueError("Go module version must be a semantic version")
        normalized = version if version.startswith("v") else f"v{version}"
        return (
            normalized if self.path == "." else f"{self.path.rstrip('/')}/{normalized}"
        )


def _build_output(
    output: str,
    target: GoTarget | None,
    host: ExecutionHost | None = None,
) -> str:
    if target is not None:
        os_name, arch_name = target.os.value, target.arch.value
    elif host is not None:
        os_name = {
            OperatingSystem.LINUX: "linux",
            OperatingSystem.MACOS: "darwin",
            OperatingSystem.WINDOWS: "windows",
        }.get(host.os, "unknown")
        arch_name = {
            Architecture.X86_64: "amd64",
            Architecture.ARM64: "arm64",
        }.get(host.architecture, "unknown")
    else:
        return output
    if "{os}" in output and os_name == "unknown":
        raise ValueError("Cannot name Go output from an unknown host OS")
    if "{arch}" in output and arch_name == "unknown":
        raise ValueError("Cannot name Go output from an unknown host architecture")
    output = output.replace("{os}", os_name).replace("{arch}", arch_name)
    extension = (
        ".exe"
        if os_name == "windows"
        else ".wasm"
        if os_name in {"js", "wasip1"}
        else ""
    )
    return (
        output
        if not extension or output.lower().endswith(extension)
        else output + extension
    )


@dataclass(frozen=True, slots=True)
class GoToolchain:
    version: str | None = None
    version_file: str | None = None
    cache: bool = False
    cache_dependency_path: str | None = None
    name: str = "go/toolchain"

    def __post_init__(self) -> None:
        if (self.version is None) == (self.version_file is None):
            raise ValueError("GoToolchain requires exactly one version source")
        if self.version is not None:
            exact_version(self.version, tool="Go")
        if self.version_file is not None:
            relative_file(self.version_file, field_name="Go version_file")
        if self.cache_dependency_path is not None:
            relative_file(
                self.cache_dependency_path, field_name="Go cache_dependency_path"
            )
            if not self.cache:
                raise ValueError("Go cache_dependency_path requires cache=True")

    def github(self) -> GitHubActionStep:
        inputs: dict[str, object] = {"cache": self.cache}
        if self.version is not None:
            inputs["go-version"] = self.version
        else:
            inputs["go-version-file"] = self.version_file
        if self.cache_dependency_path is not None:
            inputs["cache-dependency-path"] = self.cache_dependency_path
        return GitHubActionStep("setup-go", "Set up Go", inputs)


class Go(ToolFacade):
    """Imperative Go capabilities."""

    def fmt(self) -> None:
        files = sorted(
            path.relative_to(self.context.workspace).as_posix()
            for path in self.context.workspace.rglob("*.go")
            if not any(
                part.startswith(".") or part == "vendor"
                for part in path.relative_to(self.context.workspace).parts[:-1]
            )
        )
        if files:
            output = self._run(["gofmt", "-l", *files])
            if output and output.strip():
                raise TaskFailure(f"Go files need formatting:\n{output.strip()}")

    def mod_download(self) -> None:
        self._run(["go", "mod", "download"])

    def vet(self, *, packages: Iterable[str] = ("./...",)) -> None:
        self._run(["go", "vet", *tuple(packages)])

    def test(
        self,
        *,
        packages: Iterable[str] = ("./...",),
        race: bool = False,
        timeout: str | None = None,
        tags: Iterable[str] = (),
    ) -> None:
        command = ["go", "test"]
        if race:
            command.append("-race")
        if timeout is not None:
            if (
                re.fullmatch(
                    r"\d+(?:ns|us|ms|s|m|h)(?:\d+(?:ns|us|ms|s|m|h))*", timeout
                )
                is None
            ):
                raise ValueError("Go test timeout must be a Go duration")
            command.extend(["-timeout", timeout])
        selected_tags = tuple(tags)
        if selected_tags:
            command.extend(["-tags", ",".join(selected_tags)])
        self._run([*command, *tuple(packages)])

    def build(
        self,
        *,
        output: str,
        package: str = ".",
        target: GoTarget | None = None,
        cgo_enabled: bool | None = None,
        tags: Iterable[str] = (),
        ldflags: str | None = None,
        trimpath: bool = False,
    ) -> None:
        output = relative_file(output, field_name="Go build output")
        output = _build_output(output, target, self.context.host)
        command = ["go", "build"]
        if trimpath:
            command.append("-trimpath")
        selected_tags = tuple(tags)
        if selected_tags:
            command.extend(["-tags", ",".join(selected_tags)])
        if ldflags is not None:
            command.extend(["-ldflags", ldflags])
        command.extend(["-o", output, package])
        environment = {} if target is None else target.environment
        if cgo_enabled is not None:
            environment["CGO_ENABLED"] = "1" if cgo_enabled else "0"
        self._run(
            command,
            env=environment,
        )
        self.context.artifacts.add(output)


@dataclass(frozen=True)
class GoFmt:
    name: str = "go-fmt"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.CHECK:
            raise WorkflowError("GoFmt can only be used in the check stage")
        return [NodeSpec(self.name, stage, "go/fmt")]


@dataclass(frozen=True)
class GoModDownload:
    name: str = "go-mod-download"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.CHECK:
            raise WorkflowError("GoModDownload can only be used in the check stage")
        return [NodeSpec(self.name, stage, "go/mod-download")]


@dataclass(frozen=True)
class GoVet:
    packages: tuple[str, ...] = ("./...",)
    name: str = "go-vet"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.CHECK:
            raise WorkflowError("GoVet can only be used in the check stage")
        return [NodeSpec(self.name, stage, "go/vet", {"packages": list(self.packages)})]


@dataclass(frozen=True)
class GoTest:
    packages: tuple[str, ...] = ("./...",)
    race: bool = False
    timeout: str | None = None
    tags: tuple[str, ...] = ()
    name: str = "go-test"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.CHECK:
            raise WorkflowError("GoTest can only be used in the check stage")
        return [
            NodeSpec(
                self.name,
                stage,
                "go/test",
                {
                    "packages": list(self.packages),
                    "race": self.race,
                    "timeout": self.timeout,
                    "tags": list(self.tags),
                },
            )
        ]


@dataclass(frozen=True)
class GoBuild:
    output: str
    package: str = "."
    target: GoTarget | None = None
    cgo_enabled: bool | None = None
    tags: tuple[str, ...] = ()
    ldflags: str | None = None
    trimpath: bool = False
    name: str = "go-build"

    def __post_init__(self) -> None:
        relative_file(self.output, field_name="Go build output")
        if any(
            match not in {"{os}", "{arch}"}
            for match in re.findall(r"\{[^{}]+\}", self.output)
        ):
            raise ValueError(
                "Go build output supports only {os} and {arch} placeholders"
            )

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.BUILD:
            raise WorkflowError("GoBuild can only be used in the build stage")
        output = self.output
        output = _build_output(output, self.target)
        params: dict[str, object] = {
            "output": output,
            "package": self.package,
        }
        if self.target is not None:
            params["goos"] = self.target.os.value
            params["goarch"] = self.target.arch.value
        if self.cgo_enabled is not None:
            params["cgo_enabled"] = self.cgo_enabled
        if self.tags:
            params["tags"] = list(self.tags)
        if self.ldflags is not None:
            params["ldflags"] = self.ldflags
        if self.trimpath:
            params["trimpath"] = True
        return [NodeSpec(self.name, stage, "go/build", params)]


class _TestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packages: list[str] = Field(default_factory=lambda: ["./..."])
    race: bool = False
    timeout: str | None = None
    tags: list[str] = Field(default_factory=list)


class _VetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packages: list[str] = Field(default_factory=lambda: ["./..."])


class _EmptyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _BuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output: str = Field(min_length=1)
    package: str = Field(default=".", min_length=1)
    goos: GoOS | None = None
    goarch: GoArch | None = None
    cgo_enabled: bool | None = None
    tags: list[str] = Field(default_factory=list)
    ldflags: str | None = None
    trimpath: bool = False

    def target(self) -> GoTarget | None:
        if (self.goos is None) != (self.goarch is None):
            raise ValueError("goos and goarch must be provided together")
        if self.goos is None:
            return None
        return GoTarget(self.goos, self.goarch)


def _operations() -> tuple[FacadeOperation, ...]:
    return (
        FacadeOperation(
            "go/fmt", Stage.CHECK, _EmptyConfig, lambda ctx, cfg: Go(ctx).fmt()
        ),
        FacadeOperation(
            "go/mod-download",
            Stage.CHECK,
            _EmptyConfig,
            lambda ctx, cfg: Go(ctx).mod_download(),
        ),
        FacadeOperation(
            "go/vet",
            Stage.CHECK,
            _VetConfig,
            lambda ctx, cfg: Go(ctx).vet(packages=cfg.packages),
        ),
        FacadeOperation(
            "go/test",
            Stage.CHECK,
            _TestConfig,
            lambda ctx, cfg: Go(ctx).test(
                packages=cfg.packages,
                race=cfg.race,
                timeout=cfg.timeout,
                tags=cfg.tags,
            ),
        ),
        FacadeOperation(
            "go/build",
            Stage.BUILD,
            _BuildConfig,
            lambda ctx, cfg: Go(ctx).build(
                output=cfg.output,
                package=cfg.package,
                target=cfg.target(),
                cgo_enabled=cfg.cgo_enabled,
                tags=cfg.tags,
                ldflags=cfg.ldflags,
                trimpath=cfg.trimpath,
            ),
        ),
    )


def register_go_plugin(registry: PluginRegistry) -> None:
    for operation in _operations():
        registry.register_operation(
            operation,
            OperationDefinition(
                name=operation.name,
                stages=operation.stages,
                description=f"Run {operation.name}",
                config_model=operation.config_model,
            ),
        )
    registry.register_requirement_resolver(
        "github/actions",
        GoToolchain,
        lambda requirement: requirement.github(),
    )


__all__ = [
    "Go",
    "GoArch",
    "GoBuild",
    "GoFmt",
    "GoModDownload",
    "GoModule",
    "GoOS",
    "GoTarget",
    "GoTest",
    "GoToolchain",
    "GoVet",
    "register_go_plugin",
]
