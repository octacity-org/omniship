import runpy
from pathlib import Path
from zipfile import ZipFile

import pytest

from omniship.core.context import ExecutionContext
from omniship.core.node import NodeInputs
from omniship.core.result import NodeStatus
from omniship.core.stage import Stage
from omniship.plugins.discovery import load_plugins
from omniship.plugins.github.actions import GitHubActionsGenerator
from omniship.plugins.maturin import (
    Maturin,
    MaturinToolchain,
    MaturinWheel,
    register_maturin_plugin,
)
from omniship.plugins.registry import PluginRegistry
from omniship.runtime import TaskContext, TaskFailure
from omniship.workflow.compiler import compile_pipeline


def wheel(path):
    with ZipFile(path, "w") as archive:
        archive.writestr("demo-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        archive.writestr("demo-1.0.dist-info/METADATA", "Name: demo\nVersion: 1.0\n")
        archive.writestr("demo-1.0.dist-info/RECORD", "")


def test_example_generates_separate_builds_and_existing_publisher():
    source = Path("examples/maturin-release/workflow.py").resolve()
    pipeline = runpy.run_path(str(source))["pipeline"]
    config = compile_pipeline(pipeline, source)
    generated = GitHubActionsGenerator(load_plugins()).generate(
        config, source, source.parent / "omniship.yaml", pipeline
    )
    build = next(item.content for item in generated if item.path.name == "build.yml")
    assert "maturin==1.9.4" in build
    assert "windows-2025" in build
    assert config.ship["pypi-publish"].uses == "python/pypi-publish"


def test_maturin_exact_toolchain_and_typed_config(tmp_path):
    with pytest.raises(ValueError, match="exact"):
        MaturinToolchain(version="latest")
    assert "maturin==1.9.4" in MaturinToolchain(version="1.9.4").github()["run"]
    spec = MaturinWheel(
        features=("pyo3/abi3-py39",), compatibility="manylinux2014"
    ).compile(Stage.BUILD, tmp_path)[0]
    assert spec.uses == "maturin/wheel"
    assert spec.params["features"] == ["pyo3/abi3-py39"]
    with pytest.raises(ValueError):
        MaturinWheel(output="../outside").compile(Stage.BUILD, tmp_path)


def test_fresh_wheels_with_target_interpreters_and_abi3_features(tmp_path, monkeypatch):
    (tmp_path / "dist").mkdir()
    wheel(tmp_path / "dist/stale.whl")
    calls = []

    def run(self, args):
        calls.append(args)
        wheel(
            Path(args[args.index("--out") + 1]) / "demo-1.0-cp39-abi3-linux_x86_64.whl"
        )
        return "build log"

    monkeypatch.setattr(Maturin, "_run", run)
    ctx = TaskContext(tmp_path, {})
    result = Maturin(ctx).build_wheels(
        target="x86_64-unknown-linux-gnu",
        interpreters=("python3.14",),
        features=("pyo3/abi3-py39",),
        compatibility="manylinux2014",
    )
    assert [item.name for item in result] == ["demo-1.0-cp39-abi3-linux_x86_64.whl"]
    assert "--release" in calls[0]
    assert calls[0][calls[0].index("--target") + 1] == "x86_64-unknown-linux-gnu"
    assert calls[0][calls[0].index("--features") + 1] == "pyo3/abi3-py39"
    assert "python3.14" in calls[0]


@pytest.mark.parametrize("invalid", [False, True])
def test_rejects_missing_or_invalid_wheels(tmp_path, monkeypatch, invalid):
    def run(self, args):
        if invalid:
            (Path(args[args.index("--out") + 1]) / "broken.whl").write_bytes(
                b"not a wheel"
            )
        return ""

    monkeypatch.setattr(Maturin, "_run", run)
    with pytest.raises(TaskFailure, match="wheel"):
        Maturin(TaskContext(tmp_path, {})).build_wheels()


@pytest.mark.asyncio
async def test_registered_operation_uses_imperative_facade(tmp_path, monkeypatch):
    registry = PluginRegistry()
    register_maturin_plugin(registry)
    calls = []
    monkeypatch.setattr(
        Maturin, "build_wheels", lambda self, **kwargs: calls.append(kwargs)
    )
    operation = registry.get_operation("maturin/wheel")
    result = await operation.execute(
        ExecutionContext(tmp_path, Stage.BUILD),
        NodeInputs(params={"features": ["pyo3/abi3-py39"]}),
    )
    assert result.status == NodeStatus.SUCCESS
    assert calls[0]["features"] == ("pyo3/abi3-py39",)
