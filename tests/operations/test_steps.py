import asyncio
import sys
from pathlib import Path

import pytest

from omniship import Pipeline
from omniship.core.context import ExecutionContext
from omniship.core.executor import StageExecutor
from omniship.core.input import ref_template
from omniship.core.node import Node
from omniship.core.result import NodeResult, NodeStatus
from omniship.core.stage import Stage
from omniship.operations import register_core_plugin
from omniship.plugins.go import GoTest, GoToolchain, GoVet
from omniship.plugins.packaging import TarGz, register_packaging_plugin
from omniship.plugins.registry import PluginRegistry
from omniship.workflow import Command, Steps, WorkflowError
from omniship.workflow.compiler import compile_pipeline

PYTHON = sys.executable


def _registry() -> PluginRegistry:
    registry = PluginRegistry()
    register_core_plugin(registry)
    register_packaging_plugin(registry)
    return registry


def _run(
    tmp_path: Path, params: dict, stage: Stage = Stage.BUILD, env: dict | None = None
) -> NodeResult:
    node = Node(id="steps", stage=stage, operation_name="core/steps", inputs=params)
    context = ExecutionContext(tmp_path, stage, env=env or {})
    return asyncio.run(StageExecutor(_registry()).execute_node(node, context))


def _params(*blocks: object, stage: Stage = Stage.BUILD, root: Path) -> dict:
    (spec,) = Steps(*blocks, name="steps").compile(stage, root)
    return spec.params


def test_steps_compile_to_one_node_with_every_child_operation(tmp_path: Path) -> None:
    (spec,) = Steps(
        Command("make zova", name="zova"), GoTest(verbose=True), GoVet(), name="native"
    ).compile(Stage.CHECK, tmp_path)

    assert spec.name == "native"
    assert spec.uses == "core/steps"
    assert spec.params == {
        "steps": [
            {"uses": "core/command", "with": {"run": "make zova"}},
            {
                "uses": "go/test",
                "with": {
                    "packages": ["./..."],
                    "race": False,
                    "timeout": None,
                    "tags": [],
                    "verbose": True,
                },
            },
            {"uses": "go/vet", "with": {"packages": ["./..."]}},
        ]
    }


def test_steps_run_in_order_and_later_steps_see_earlier_files(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    params = _params(
        Command(
            [PYTHON, "-c", "open('lib/zova.a', 'w').write('lib')"],
            artifacts=["lib/zova.a"],
        ),
        TarGz(output="dist/bundle-{version}.tar.gz", files={"lib/zova.a": "zova.a"}),
        root=tmp_path,
    )

    result = _run(
        tmp_path,
        params,
        env={"OMNISHIP_REF_TYPE": "tag", "OMNISHIP_REF_NAME": "v1.0.0"},
    )

    assert result.status == NodeStatus.SUCCESS, result.error_message
    assert sorted(artifact.path.name for artifact in result.artifacts) == [
        "bundle-1.0.0.tar.gz",
        "zova.a",
    ]


def test_steps_stop_at_the_first_failure(tmp_path: Path) -> None:
    params = _params(
        Command([PYTHON, "-c", "raise SystemExit(3)"], name="first"),
        Command([PYTHON, "-c", "open('ran', 'w').close()"], name="second"),
        root=tmp_path,
    )

    result = _run(tmp_path, params)

    assert result.status == NodeStatus.FAILED
    assert "Step 1 (core/command) failed" in (result.error_message or "")
    assert "exit code 3" in (result.error_message or "")
    assert not (tmp_path / "ran").exists()


def test_steps_reject_unknown_operations_and_wrong_stages(tmp_path: Path) -> None:
    unknown = _run(tmp_path, {"steps": [{"uses": "nope/missing", "with": {}}]})
    wrong_stage = _run(
        tmp_path,
        {"steps": [{"uses": "packaging/zip", "with": {}}]},
        stage=Stage.CHECK,
    )

    assert unknown.status == NodeStatus.FAILED
    assert "Step 1 uses 'nope/missing', which is not registered" in (
        unknown.error_message or ""
    )
    assert wrong_stage.status == NodeStatus.FAILED
    assert "cannot run in stage 'check'" in (wrong_stage.error_message or "")


def test_steps_need_at_least_one_single_node_block(tmp_path: Path) -> None:
    class TwoNodes:
        name = "two"

        def compile(self, stage, workspace_root):
            return [*Command("a").compile(stage, workspace_root)] * 2

    with pytest.raises(WorkflowError, match="at least one step"):
        Steps(name="empty")
    with pytest.raises(WorkflowError, match="compiles to 2 tasks"):
        Steps(TwoNodes(), name="steps").compile(Stage.CHECK, tmp_path)
    with pytest.raises(WorkflowError, match="only be used in the build stage"):
        Steps(TarGz(output="a.tar.gz", files={}), name="s").compile(
            Stage.CHECK, tmp_path
        )


def test_steps_task_keeps_requirements_and_placement(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        stage.task(
            Steps(Command("make"), GoTest(), name="native-tests"),
            requires=[GoToolchain(version="1.27.0")],
        )

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    node = config.check["native-tests"]
    assert node.uses == "core/steps"
    assert len(node.requirements) == 1


def test_command_block_compiles_to_core_command(tmp_path: Path) -> None:
    (spec,) = Command(
        ["make", "dist"],
        name="dist",
        cwd="app",
        env={"MODE": "release"},
        artifacts=["app/dist/{version}.tar"],
        timeout=60,
    ).compile(Stage.BUILD, tmp_path)

    assert spec.name == "dist"
    assert spec.uses == "core/command"
    assert spec.params == {
        "run": ["make", "dist"],
        "cwd": "app",
        "env": {"MODE": "release"},
        "artifacts": ref_template(["app/dist/{version}.tar"]),
        "timeout": 60,
    }
