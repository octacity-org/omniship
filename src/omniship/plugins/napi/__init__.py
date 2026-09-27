"""NAPI-RS native builds and offline npm package assembly.

Publication belongs to ``omniship.plugins.node.NpmPublish``. Node and Rust
toolchains are independent requirements; this plugin only installs the napi CLI.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from omniship.core.artifact import Artifact
from omniship.core.stage import Stage
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


@dataclass(frozen=True)
class NapiToolchain:
    version: str
    name: str = "napi/toolchain"

    def __post_init__(self) -> None:
        exact_version(self.version, tool="NAPI-RS")

    def github(self) -> dict[str, str]:
        return {
            "name": "Set up NAPI-RS",
            "run": f"npm install --global @napi-rs/cli@{self.version} --ignore-scripts",
        }


class _BuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(pattern=r"^[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+$")
    output: str = "dist/napi"
    features: tuple[str, ...] = ()
    release: bool = True


class _PackageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifacts: tuple[str, ...] = Field(min_length=1)
    output: str = "dist/npm"


class Napi(ToolFacade):
    """Imperative native build and package assembly, never publication."""

    def _directory(self, value: str) -> Path:
        path = (
            self.context.workspace / relative_file(value, field_name="output")
        ).resolve()
        if not path.is_relative_to(self.context.workspace):
            raise TaskFailure("output escapes workspace")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def build(
        self,
        *,
        target: str,
        output: str = "dist/napi",
        features: tuple[str, ...] = (),
        release: bool = True,
    ) -> Artifact:
        """Build into a fresh directory, retaining the binary, JS loader and types."""
        cfg = _BuildConfig(
            target=target, output=output, features=features, release=release
        )
        destination = self._directory(cfg.output)
        with TemporaryDirectory(dir=destination) as temporary:
            arguments = [
                "napi",
                "build",
                "--platform",
                "--target",
                cfg.target,
                "--output-dir",
                temporary,
            ]
            if cfg.release:
                arguments.append("--release")
            if cfg.features:
                arguments.extend(["--features", *cfg.features])
            self._run(arguments)
            if not list(Path(temporary).glob("*.node")):
                raise TaskFailure("NAPI-RS produced no native .node binary")
            result = destination / f"{cfg.target}-{uuid4().hex}"
            shutil.copytree(temporary, result)
        return self.context.artifacts.add(result, name=f"napi-{cfg.target}")

    def package(
        self, *, artifacts: tuple[str, ...], output: str = "dist/npm"
    ) -> tuple[Artifact, ...]:
        """Assemble every configured native platform and pack platform packages first.

        Reads package name/version/targets from package.json. All requested build
        artifacts must exist and every generated platform package must have a
        binary. No pre-publish hooks run. Generated bindings are restored to the
        workspace; the source package.json is restored even on packing failure.
        """
        cfg = _PackageConfig(artifacts=artifacts, output=output)
        selected = []
        for name in cfg.artifacts:
            if sum(item.name == name for item in self.context.artifacts) > 1:
                raise TaskFailure(f"NAPI-RS artifact name is ambiguous: {name}")
            artifact = self.context.artifacts.get(name)
            if artifact is None or not artifact.path.exists():
                raise TaskFailure(f"Missing NAPI-RS artifact: {name}")
            selected.append(artifact.path)
        destination = self._directory(cfg.output)
        package_file = self.context.workspace / "package.json"
        if package_file.is_symlink():
            raise TaskFailure("package.json must not be a symlink")
        original = package_file.read_bytes()
        root = json.loads(original)
        with TemporaryDirectory(dir=destination) as temporary:
            work = Path(temporary)
            binaries = work / "binaries"
            packages = work / "npm"
            packed = work / "packed"
            for directory in (binaries, packages, packed):
                directory.mkdir()
            files: dict[str, Path] = {}
            for artifact in selected:
                candidates = artifact.rglob("*") if artifact.is_dir() else (artifact,)
                for candidate in candidates:
                    if not candidate.is_file() or candidate.suffix not in {
                        ".node",
                        ".js",
                        ".ts",
                    }:
                        continue
                    if candidate.is_symlink() or not candidate.resolve().is_relative_to(
                        self.context.workspace
                    ):
                        raise TaskFailure("NAPI-RS artifact escapes workspace")
                    previous = files.get(candidate.name)
                    if (
                        previous is not None
                        and previous.read_bytes() != candidate.read_bytes()
                    ):
                        raise TaskFailure(
                            f"Conflicting NAPI-RS artifact: {candidate.name}"
                        )
                    files[candidate.name] = candidate
            if not any(name.endswith(".node") for name in files):
                raise TaskFailure("NAPI-RS artifacts contain no native binaries")
            for name, source in files.items():
                shutil.copy2(source, binaries / name)
            # CLI 3.0.0 joins these paths to cwd rather than resolving them;
            # absolute paths would duplicate the workspace prefix.
            packages_arg = packages.relative_to(self.context.workspace).as_posix()
            binaries_arg = binaries.relative_to(self.context.workspace).as_posix()
            self._run(["napi", "create-npm-dirs", "--npm-dir", packages_arg])
            self._run(
                [
                    "napi",
                    "artifacts",
                    "--output-dir",
                    binaries_arg,
                    "--npm-dir",
                    packages_arg,
                ]
            )
            self._run(["napi", "version", "--npm-dir", packages_arg])
            platform_dirs = sorted(
                path.parent for path in packages.glob("*/package.json")
            )
            if not platform_dirs:
                raise TaskFailure(
                    "NAPI-RS generated no platform packages; configure napi.targets"
                )
            optional = dict(root.get("optionalDependencies", {}))
            for directory in platform_dirs:
                if not list(directory.glob("*.node")):
                    raise TaskFailure(f"Missing native binary for {directory.name}")
                metadata = json.loads((directory / "package.json").read_text())
                if metadata["version"] != root["version"]:
                    raise TaskFailure(
                        "NAPI-RS platform package version differs from root"
                    )
                optional[metadata["name"]] = root["version"]
            for name, source in files.items():
                if not name.endswith(".node"):
                    target = self.context.workspace / name
                    if target.is_symlink():
                        raise TaskFailure("Generated binding destination is a symlink")
                    shutil.copy2(source, target)
            root["optionalDependencies"] = optional
            tarballs = []
            try:
                package_file.write_text(json.dumps(root, indent=2) + "\n")
                for index, directory in enumerate(
                    (*platform_dirs, self.context.workspace)
                ):
                    pack_output = packed / str(index)
                    pack_output.mkdir()
                    self._run(
                        [
                            "npm",
                            "pack",
                            str(directory),
                            "--ignore-scripts",
                            "--pack-destination",
                            str(pack_output),
                        ]
                    )
                    candidates = list(pack_output.glob("*.tgz"))
                    if len(candidates) != 1:
                        raise TaskFailure("npm pack must produce exactly one tarball")
                    source = candidates[0]
                    filename = source.name
                    if not source.is_file() or source.is_symlink():
                        raise TaskFailure(f"npm pack did not produce {filename}")
                    final = destination / filename
                    if final.is_symlink():
                        raise TaskFailure("Tarball destination is a symlink")
                    shutil.copy2(source, final)
                    tarballs.append(final)
            finally:
                package_file.write_bytes(original)
        return tuple(
            self.context.artifacts.add(
                path, metadata={"npm.package": "true", "npm.order": str(index)}
            )
            for index, path in enumerate(tarballs)
        )


@dataclass(frozen=True)
class NapiBuild:
    target: str
    output: str = "dist/napi"
    features: tuple[str, ...] = ()
    release: bool = True
    name: str = "napi-build"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        return _compile(self, stage, "napi/build", _BuildConfig)


@dataclass(frozen=True)
class NapiPackage:
    artifacts: tuple[str, ...]
    output: str = "dist/npm"
    name: str = "napi-package"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        return _compile(self, stage, "napi/package", _PackageConfig)


def _compile(block, stage, operation, model) -> list[NodeSpec]:
    if stage != Stage.BUILD:
        raise WorkflowError(
            f"{type(block).__name__} can only be used in the build stage"
        )
    params = asdict(block)
    params.pop("name")
    cfg = model.model_validate(params)
    relative_file(cfg.output, field_name="output")
    return [NodeSpec(block.name, stage, operation, cfg.model_dump(mode="json"))]


def register_napi_plugin(registry: PluginRegistry) -> None:
    for name, model, method in (
        ("napi/build", _BuildConfig, "build"),
        ("napi/package", _PackageConfig, "package"),
    ):
        operation = FacadeOperation(
            name,
            Stage.BUILD,
            model,
            lambda ctx, cfg, method=method: getattr(Napi(ctx), method)(
                **cfg.model_dump()
            ),
        )
        registry.register_operation(
            operation,
            OperationDefinition(
                name=name,
                stages=operation.stages,
                description=f"Run {name}",
                config_model=model,
            ),
        )
    registry.register_requirement_resolver(
        "github/actions", NapiToolchain, lambda requirement: requirement.github()
    )


__all__ = ["Napi", "NapiBuild", "NapiPackage", "NapiToolchain", "register_napi_plugin"]
