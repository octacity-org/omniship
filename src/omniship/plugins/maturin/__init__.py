"""Maturin wheel builds; publication remains the Python plugin's responsibility."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

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
class MaturinToolchain:
    """Provision an exact Maturin CLI; Rust and Python remain separate choices."""

    version: str
    name: str = "maturin/toolchain"

    def __post_init__(self) -> None:
        exact_version(self.version, tool="Maturin")

    def github(self) -> dict[str, str]:
        return {
            "name": "Set up Maturin",
            "run": f"uv tool install maturin=={self.version}",
        }


class _WheelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+$"
    )
    interpreters: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    compatibility: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]+$")
    manifest_path: str = "Cargo.toml"
    output: str = "dist"
    release: bool = True


class Maturin(ToolFacade):
    """Build native Python wheels through Maturin without publishing them."""

    def build_wheels(
        self,
        *,
        target: str | None = None,
        interpreters: tuple[str, ...] = (),
        features: tuple[str, ...] = (),
        compatibility: str | None = None,
        manifest_path: str = "Cargo.toml",
        output: str = "dist",
        release: bool = True,
    ) -> tuple[Artifact, ...]:
        """Register only this invocation's wheels, checking their archive metadata.

        ABI3 is configured by Cargo features such as ``pyo3/abi3-py39``. Linux
        compatibility still requires a suitable build image/toolchain; passing
        a compatibility tag does not provision a manylinux container.
        """
        cfg = _WheelConfig(
            target=target,
            interpreters=interpreters,
            features=features,
            compatibility=compatibility,
            manifest_path=manifest_path,
            output=output,
            release=release,
        )
        destination = (
            self.context.workspace / relative_file(cfg.output, field_name="output")
        ).resolve()
        manifest = (
            self.context.workspace
            / relative_file(cfg.manifest_path, field_name="manifest_path")
        ).resolve()
        if not destination.is_relative_to(
            self.context.workspace
        ) or not manifest.is_relative_to(self.context.workspace):
            raise TaskFailure("Maturin path escapes workspace")
        destination.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=destination) as temporary:
            args = [
                "maturin",
                "build",
                "--manifest-path",
                str(manifest),
                "--out",
                temporary,
            ]
            if cfg.release:
                args.append("--release")
            if cfg.target:
                args.extend(["--target", cfg.target])
            if cfg.interpreters:
                args.extend(["--interpreter", *cfg.interpreters])
            if cfg.features:
                args.extend(["--features", ",".join(cfg.features)])
            if cfg.compatibility:
                args.extend(["--compatibility", cfg.compatibility])
            self._run(args)
            wheels = sorted(Path(temporary).glob("*.whl"))
            if not wheels:
                raise TaskFailure("Maturin produced no wheels")
            for wheel in wheels:
                _verify_wheel(wheel)
            paths = []
            for wheel in wheels:
                final = destination / wheel.name
                if final.is_symlink():
                    raise TaskFailure("Wheel destination is a symlink")
                shutil.copy2(wheel, final)
                paths.append(final)
        return tuple(self.context.artifacts.add(path) for path in paths)


def _verify_wheel(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise TaskFailure(f"Invalid wheel: {path.name}")
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            metadata = [
                name.removesuffix("WHEEL")
                for name in names
                if name.endswith(".dist-info/WHEEL")
            ]
            if len(metadata) != 1 or not {
                metadata[0] + "METADATA",
                metadata[0] + "RECORD",
            }.issubset(names):
                raise TaskFailure(f"Invalid wheel metadata: {path.name}")
            if archive.testzip() is not None:
                raise TaskFailure(f"Corrupt wheel: {path.name}")
    except BadZipFile as exc:
        raise TaskFailure(f"Invalid wheel archive: {path.name}") from exc


@dataclass(frozen=True)
class MaturinWheel:
    target: str | None = None
    interpreters: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    compatibility: str | None = None
    manifest_path: str = "Cargo.toml"
    output: str = "dist"
    release: bool = True
    name: str = "maturin-wheel"

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        if stage != Stage.BUILD:
            raise WorkflowError("MaturinWheel can only be used in the build stage")
        params = asdict(self)
        params.pop("name")
        cfg = _WheelConfig.model_validate(params)
        relative_file(cfg.output, field_name="output")
        relative_file(cfg.manifest_path, field_name="manifest_path")
        return [
            NodeSpec(self.name, stage, "maturin/wheel", cfg.model_dump(mode="json"))
        ]


def register_maturin_plugin(registry: PluginRegistry) -> None:
    operation = FacadeOperation(
        "maturin/wheel",
        Stage.BUILD,
        _WheelConfig,
        lambda ctx, cfg: Maturin(ctx).build_wheels(**cfg.model_dump()),
    )
    registry.register_operation(
        operation,
        OperationDefinition(
            name=operation.name,
            stages=operation.stages,
            description="Build native Python wheels with Maturin",
            config_model=_WheelConfig,
        ),
    )
    registry.register_requirement_resolver(
        "github/actions", MaturinToolchain, lambda requirement: requirement.github()
    )


__all__ = ["Maturin", "MaturinWheel", "MaturinToolchain", "register_maturin_plugin"]
