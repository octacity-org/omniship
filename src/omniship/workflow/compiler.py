import inspect
from pathlib import Path

from omniship.config.models import NodeConfig, OmniShipConfig
from omniship.core.requirement import merge_requirements
from omniship.core.stage import Stage

from .errors import WorkflowError
from .model import BlockDeclaration, NodeSpec, Pipeline, TaskDeclaration


def compile_pipeline(pipeline: Pipeline, source_path: str | Path) -> OmniShipConfig:
    source = Path(source_path).resolve()
    workspace_root = source.parent
    stages: dict[Stage, dict[str, NodeConfig]] = {stage: {} for stage in Stage}

    for stage in Stage:
        specs: list[NodeSpec] = []
        for entry in pipeline.entries(stage):
            if isinstance(entry, TaskDeclaration):
                if len(inspect.signature(entry.function).parameters) != 1:
                    raise WorkflowError(
                        f"Task '{entry.function.__name__}' must accept exactly one context argument"
                    )
                for dependency in entry.after:
                    if dependency.stage != stage:
                        raise WorkflowError(
                            f"Dependency '{dependency.name}' must be in the same stage"
                        )
                specs.append(
                    NodeSpec(
                        name=entry.ref.name,
                        stage=stage,
                        uses="core/python",
                        params={
                            "callable": (
                                f"{source.name}:pipeline:{stage.value}:{entry.ref.name}"
                            )
                        },
                        needs=entry.after,
                        execution=entry.execution,
                        requirements=entry.requirements,
                        forward_to_build=entry.forward_to_build,
                    )
                )
            elif isinstance(entry, BlockDeclaration):
                compiled = entry.block.compile(stage, workspace_root)
                for spec in compiled:
                    try:
                        requirements = merge_requirements(
                            spec.requirements, entry.requirements
                        )
                    except ValueError as exc:
                        raise WorkflowError(str(exc)) from exc
                    specs.append(
                        NodeSpec(
                            name=spec.name,
                            stage=spec.stage,
                            uses=spec.uses,
                            params=spec.params,
                            needs=(*spec.needs, *entry.after),
                            condition=spec.condition,
                            execution=entry.execution,
                            requirements=requirements,
                            forward_to_build=entry.forward_to_build,
                        )
                    )
        for spec in specs:
            if spec.stage != stage:
                raise WorkflowError(
                    f"Block '{spec.name}' cannot be used in stage '{stage.value}'"
                )
            if spec.name in stages[stage]:
                raise WorkflowError(
                    f"Duplicate node name '{spec.name}' in stage '{stage.value}'"
                )
            for dependency in spec.needs:
                if dependency.stage != stage:
                    raise WorkflowError(
                        f"Dependency '{dependency.name}' must be in the same stage"
                    )
            try:
                requirements = merge_requirements(spec.requirements)
            except ValueError as exc:
                raise WorkflowError(str(exc)) from exc
            stages[stage][spec.name] = NodeConfig(
                uses=spec.uses,
                needs=[dependency.name for dependency in spec.needs],
                with_=spec.params,
                if_=spec.condition,
                execution=spec.execution,
                requirements=requirements,
                forward_to_build=spec.forward_to_build,
            )

    return OmniShipConfig(
        version=1,
        logging=pipeline.logging,
        check=stages[Stage.CHECK],
        build=stages[Stage.BUILD],
        ship=stages[Stage.SHIP],
    )
