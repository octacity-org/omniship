"""Generic typed blocks for the core operations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omniship.core.input import ref_template
from omniship.core.requirement import merge_requirements
from omniship.core.stage import Stage
from omniship.workflow.errors import WorkflowError
from omniship.workflow.model import Block, NodeSpec


@dataclass(frozen=True)
class Command:
    """Run a shell command (a string) or an argument list with ``core/command``.

    ``{tag}`` and ``{version}`` in ``run``, ``env`` and ``artifacts`` are
    filled in from the pushed tag when the command runs.
    """

    run: str | Sequence[str]
    name: str = "command"
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    artifacts: Sequence[str] = ()
    timeout: float | None = None

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        run = self.run if isinstance(self.run, str) else list(self.run)
        params: dict[str, Any] = {"run": ref_template(run)}
        if self.cwd is not None:
            params["cwd"] = self.cwd
        if self.env:
            params["env"] = ref_template(dict(self.env))
        if self.artifacts:
            params["artifacts"] = ref_template(list(self.artifacts))
        if self.timeout is not None:
            params["timeout"] = self.timeout
        return [NodeSpec(self.name, stage, "core/command", params)]


class Steps:
    """Run several blocks one after another in a single task (one CI job).

    Each block must compile to one task. Later steps see the files and
    artifacts of earlier ones, and the first failing step stops the rest.
    """

    def __init__(self, *steps: Block, name: str) -> None:
        if not steps:
            raise WorkflowError(f"Steps '{name}' needs at least one step")
        self.steps = steps
        self.name = name

    def compile(self, stage: Stage, workspace_root: Path) -> list[NodeSpec]:
        entries: list[dict[str, Any]] = []
        requirements = []
        for number, block in enumerate(self.steps, start=1):
            specs = block.compile(stage, workspace_root)
            if len(specs) != 1:
                raise WorkflowError(
                    f"Step {number} of '{self.name}' compiles to {len(specs)} "
                    "tasks; each step must be one task"
                )
            (spec,) = specs
            if spec.needs or spec.condition is not None or spec.execution is not None:
                raise WorkflowError(
                    f"Step {number} of '{self.name}' sets dependencies, a "
                    "condition or an execution target, which steps cannot use"
                )
            entries.append({"uses": spec.uses, "with": spec.params})
            requirements.append(spec.requirements)
        try:
            merged = merge_requirements(*requirements)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        return [
            NodeSpec(
                self.name,
                stage,
                "core/steps",
                {"steps": entries},
                requirements=merged,
            )
        ]
