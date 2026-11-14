import asyncio
from pathlib import Path

import pytest

from omniship.core.context import ExecutionContext
from omniship.core.executor import StageExecutor
from omniship.core.input import (
    REF_TEMPLATE_KEY,
    RuntimeInputError,
    ref_template,
    resolve_ref_templates,
)
from omniship.core.node import Node, NodeInputs
from omniship.core.result import NodeResult, NodeStatus
from omniship.core.stage import Stage
from omniship.plugins.metadata import OperationDefinition
from omniship.plugins.registry import PluginRegistry

TAG_RUN = {"OMNISHIP_REF_TYPE": "tag", "OMNISHIP_REF_NAME": "v1.2.0"}


def test_ref_template_wraps_only_values_with_placeholders() -> None:
    assert ref_template("dist/app") == "dist/app"
    assert ref_template("dist/app-{os}") == "dist/app-{os}"
    assert ref_template("dist/app-{version}") == {
        REF_TEMPLATE_KEY: "dist/app-{version}"
    }
    assert ref_template({"bin/app": "app-{tag}/app"}) == {
        REF_TEMPLATE_KEY: {"bin/app": "app-{tag}/app"}
    }


def test_ref_templates_fill_tag_and_version_in_keys_and_values() -> None:
    params = {
        "output": ref_template("dist/app-{version}-{os}.tar.gz"),
        "files": ref_template({"dist/{tag}/app": "app-{version}/app"}),
        "plain": "{version} stays literal",
    }

    resolved = resolve_ref_templates(params, TAG_RUN)

    assert resolved == {
        "output": "dist/app-1.2.0-{os}.tar.gz",
        "files": {"dist/v1.2.0/app": "app-1.2.0/app"},
        "plain": "{version} stays literal",
    }


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"OMNISHIP_REF_TYPE": "branch", "OMNISHIP_REF_NAME": "main"},
        {"OMNISHIP_REF_TYPE": "tag", "OMNISHIP_REF_NAME": ""},
    ],
)
def test_ref_templates_need_a_tag_push(env: dict[str, str]) -> None:
    with pytest.raises(RuntimeInputError, match="not started by a tag push"):
        resolve_ref_templates({"output": ref_template("app-{tag}")}, env)


def test_values_without_templates_need_no_tag() -> None:
    assert resolve_ref_templates({"output": "dist/app"}, {}) == {"output": "dist/app"}


class _Recorder:
    name = "test/record"
    stages = frozenset({Stage.BUILD})
    cacheable = False

    def __init__(self) -> None:
        self.params: dict | None = None

    async def execute(
        self, context: ExecutionContext, inputs: NodeInputs
    ) -> NodeResult:
        self.params = inputs.params
        return NodeResult(status=NodeStatus.SUCCESS)


def _run(tmp_path: Path, env: dict[str, str]) -> tuple[_Recorder, NodeResult]:
    registry = PluginRegistry()
    recorder = _Recorder()
    registry.register_operation(
        recorder, OperationDefinition(name=recorder.name, stages=recorder.stages)
    )
    node = Node(
        id="archive",
        stage=Stage.BUILD,
        operation_name=recorder.name,
        inputs={"output": ref_template("dist/app-{version}.zip")},
    )
    context = ExecutionContext(tmp_path, Stage.BUILD, env=env)
    result = asyncio.run(StageExecutor(registry).execute_node(node, context))
    return recorder, result


def test_executor_fills_ref_templates_before_the_operation_runs(
    tmp_path: Path,
) -> None:
    recorder, result = _run(tmp_path, TAG_RUN)

    assert result.status == NodeStatus.SUCCESS
    assert recorder.params == {"output": "dist/app-1.2.0.zip"}


def test_executor_fails_a_template_node_outside_tag_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OMNISHIP_REF_TYPE", raising=False)
    monkeypatch.delenv("OMNISHIP_REF_NAME", raising=False)

    recorder, result = _run(tmp_path, {})

    assert result.status == NodeStatus.FAILED
    assert "not started by a tag push" in (result.error_message or "")
    assert recorder.params is None
