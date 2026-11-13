from pathlib import Path

import pytest

from omniship.core.artifact import Artifact
from omniship.core.context import ExecutionContext
from omniship.core.execution import Platform
from omniship.core.logging import LogLevel, LogRecord
from omniship.core.node import NodeInputs
from omniship.core.stage import Stage
from omniship.operations.python_task import PythonTaskOperation


@pytest.mark.asyncio
async def test_python_task_executes_and_collects_artifacts(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.py"
    workflow.write_text(
        "def package(ctx):\n"
        "    output = ctx.workspace / 'dist.whl'\n"
        "    output.write_text('wheel')\n"
        "    ctx.log.info('built wheel')\n"
        "    ctx.artifacts.add(output)\n",
        encoding="utf-8",
    )
    context = ExecutionContext(workspace_root=tmp_path, stage=Stage.BUILD)

    result = await PythonTaskOperation().execute(
        context,
        NodeInputs(params={"callable": "workflow.py:package"}),
    )

    assert result.is_success
    assert result.stdout == "built wheel"
    assert [artifact.name for artifact in result.artifacts] == ["dist.whl"]


@pytest.mark.asyncio
async def test_python_task_tags_cross_compiled_artifacts(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.py"
    workflow.write_text(
        "from omniship import Platform\n"
        "def package(ctx):\n"
        "    output = ctx.workspace / 'zova.lib'\n"
        "    output.write_text('lib')\n"
        "    ctx.artifacts.add(output, platform=Platform.WINDOWS_X86_64)\n",
        encoding="utf-8",
    )
    context = ExecutionContext(workspace_root=tmp_path, stage=Stage.BUILD)

    result = await PythonTaskOperation().execute(
        context,
        NodeInputs(params={"callable": "workflow.py:package"}),
    )
    context.record_result("package", result)

    assert result.is_success
    assert context.artifacts.get("zova.lib").platform is Platform.WINDOWS_X86_64


@pytest.mark.asyncio
async def test_python_task_rejects_platform_strings(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.py"
    workflow.write_text(
        "def package(ctx):\n"
        "    output = ctx.workspace / 'zova.lib'\n"
        "    output.write_text('lib')\n"
        "    ctx.artifacts.add(output, platform='windows/x86_64')\n",
        encoding="utf-8",
    )
    context = ExecutionContext(workspace_root=tmp_path, stage=Stage.BUILD)

    result = await PythonTaskOperation().execute(
        context,
        NodeInputs(params={"callable": "workflow.py:package"}),
    )

    assert not result.is_success
    assert "must be a Platform member" in (result.error_message or "")


@pytest.mark.asyncio
async def test_python_task_emits_optional_structured_logs(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.py"
    workflow.write_text(
        "def check(ctx):\n"
        "    ctx.log.debug('details')\n"
        "    ctx.log.info('checking')\n"
        "    ctx.log.warning('careful')\n"
        "    ctx.log.error('reported but not failed')\n",
        encoding="utf-8",
    )
    records: list[LogRecord] = []
    context = ExecutionContext(
        workspace_root=tmp_path,
        stage=Stage.CHECK,
        task_id="custom-check",
        log_sink=records.append,
    )

    result = await PythonTaskOperation().execute(
        context,
        NodeInputs(params={"callable": "workflow.py:check"}),
    )

    assert result.is_success
    assert [record.level for record in records] == [
        LogLevel.DEBUG,
        LogLevel.INFO,
        LogLevel.WARNING,
        LogLevel.ERROR,
    ]
    assert all(record.stage == Stage.CHECK for record in records)
    assert all(record.task == "custom-check" for record in records)


@pytest.mark.asyncio
async def test_python_task_turns_exceptions_into_failures(tmp_path: Path) -> None:
    (tmp_path / "workflow.py").write_text(
        "def fail(ctx):\n    raise ValueError('broken release')\n",
        encoding="utf-8",
    )
    context = ExecutionContext(workspace_root=tmp_path, stage=Stage.CHECK)

    result = await PythonTaskOperation().execute(
        context,
        NodeInputs(params={"callable": "workflow.py:fail"}),
    )

    assert result.is_failed
    assert result.error_message == "ValueError: broken release"


@pytest.mark.asyncio
async def test_decorated_task_remains_importable_at_execution_time(
    tmp_path: Path,
) -> None:
    (tmp_path / "workflow.py").write_text(
        "from omniship import Pipeline\n"
        "pipeline = Pipeline()\n"
        "@pipeline.build\n"
        "def build(stage):\n"
        "    @stage.task\n"
        "    def package(ctx):\n"
        "        ctx.log.info('decorated task ran')\n",
        encoding="utf-8",
    )
    context = ExecutionContext(workspace_root=tmp_path, stage=Stage.BUILD)

    result = await PythonTaskOperation().execute(
        context,
        NodeInputs(params={"callable": "workflow.py:pipeline:build:package"}),
    )

    assert result.is_success
    assert result.stdout == "decorated task ran"


@pytest.mark.asyncio
async def test_python_task_can_read_artifacts_from_earlier_stages(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing.whl"
    existing.write_text("wheel", encoding="utf-8")
    (tmp_path / "workflow.py").write_text(
        "def publish(ctx):\n"
        "    names = [artifact.name for artifact in ctx.artifacts]\n"
        "    ctx.log.info(','.join(names))\n",
        encoding="utf-8",
    )
    context = ExecutionContext(workspace_root=tmp_path, stage=Stage.SHIP)
    context.artifacts.add(Artifact.from_path(existing))

    result = await PythonTaskOperation().execute(
        context,
        NodeInputs(params={"callable": "workflow.py:publish"}),
    )

    assert result.is_success
    assert result.stdout == "existing.whl"
    assert result.artifacts == ()
