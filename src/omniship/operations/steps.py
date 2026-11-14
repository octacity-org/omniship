import time
from dataclasses import replace
from typing import Any

from pydantic import BaseModel, Field

from omniship.core.artifact import Artifact, ArtifactSet
from omniship.core.context import ExecutionContext
from omniship.core.node import NodeInputs
from omniship.core.result import NodeResult, NodeStatus
from omniship.core.stage import Stage
from omniship.plugins.metadata import OperationDefinition
from omniship.plugins.registry import PluginRegistry


class StepConfig(BaseModel):
    uses: str = Field(..., description="Operation that runs this step")
    with_: dict[str, Any] = Field(
        default_factory=dict, alias="with", description="Operation parameters"
    )


class StepsConfig(BaseModel):
    steps: list[StepConfig] = Field(
        ..., min_length=1, description="Operations to run in order"
    )


class StepsOperation:
    """Run several operations in order inside one task."""

    name: str = "core/steps"
    stages: frozenset[Stage] = frozenset({Stage.CHECK, Stage.BUILD, Stage.SHIP})
    cacheable: bool = False

    def __init__(self, registry: PluginRegistry) -> None:
        self.registry = registry

    async def execute(
        self, context: ExecutionContext, inputs: NodeInputs
    ) -> NodeResult:
        start_time = time.monotonic()
        try:
            cfg = StepsConfig.model_validate(inputs.params)
        except Exception as exc:
            return NodeResult(
                status=NodeStatus.FAILED,
                error_message=f"Invalid steps configuration: {exc}",
            )

        # Later steps see what earlier steps produced.
        available = ArtifactSet()
        for artifact in inputs.artifacts:
            available.add(artifact)
        produced: list[Artifact] = []
        outputs: dict[str, Any] = {}
        stdout: list[str] = []
        stderr: list[str] = []

        def finish(status: NodeStatus, error: str | None = None) -> NodeResult:
            return NodeResult(
                status=status,
                duration=time.monotonic() - start_time,
                outputs=outputs,
                artifacts=tuple(produced),
                error_message=error,
                stdout="".join(stdout),
                stderr="".join(stderr),
            )

        for number, step in enumerate(cfg.steps, start=1):
            if not self.registry.has_operation(step.uses):
                return finish(
                    NodeStatus.FAILED,
                    f"Step {number} uses '{step.uses}', which is not registered",
                )
            operation = self.registry.get_operation(step.uses)
            if context.stage not in operation.stages:
                return finish(
                    NodeStatus.FAILED,
                    f"Step {number} ({step.uses}) cannot run in stage "
                    f"'{context.stage.value}'",
                )
            try:
                result = await operation.execute(
                    replace(context, artifacts=available),
                    NodeInputs(params=step.with_, artifacts=available.to_list()),
                )
            except Exception as exc:
                result = NodeResult(status=NodeStatus.FAILED, error_message=str(exc))
            stdout.append(result.stdout)
            stderr.append(result.stderr)
            outputs.update(result.outputs)
            for artifact in result.artifacts:
                available.add(artifact)
                produced.append(artifact)
            if not result.is_success:
                return finish(
                    NodeStatus.FAILED,
                    f"Step {number} ({step.uses}) failed: "
                    f"{result.error_message or result.status.value}",
                )
        return finish(NodeStatus.SUCCESS)


def get_steps_definition() -> OperationDefinition:
    return OperationDefinition(
        name=StepsOperation.name,
        stages=StepsOperation.stages,
        description="Run several operations in order inside one task",
        config_model=StepsConfig,
        cacheable=False,
    )
