from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from omniship.core.context import ExecutionContext
from omniship.core.logging import LogLevel
from omniship.core.node import NodeInputs
from omniship.core.result import NodeResult
from omniship.core.stage import Stage

if TYPE_CHECKING:
    from omniship.config.models import OmniShipConfig
    from omniship.plugins.registry import PluginRegistry
    from omniship.workflow.model import Pipeline


class Operation(Protocol):
    name: str
    stages: frozenset[Stage]
    cacheable: bool = False

    async def execute(
        self,
        context: ExecutionContext,
        inputs: NodeInputs,
    ) -> NodeResult: ...


@dataclass(frozen=True)
class GeneratedFile:
    path: Path
    content: str


# Each CI job runs one node on its own, so a node skipped by its condition
# could not skip the jobs that depend on it.
CONDITION_UNSUPPORTED = (
    "has a condition, which generated CI jobs cannot apply: jobs that "
    "depend on it would still run when it is skipped"
)


class WorkflowGenerator(Protocol):
    name: str

    def generate(
        self,
        config: OmniShipConfig,
        source_path: Path,
        config_path: Path,
        pipeline: Pipeline,
    ) -> tuple[GeneratedFile, ...]: ...


class LockUpdater(Protocol):
    """Refreshes the dependency pins a plugin keeps in the lock file."""

    name: str

    def update(
        self,
        path: Path,
        dependency: str | None,
        echo: Callable[[str], None],
    ) -> GeneratedFile: ...


class LogAnnotator(Protocol):
    """Formats warnings and errors for a CI provider's log viewer."""

    name: str

    def active(self, env: Mapping[str, str]) -> bool: ...

    def annotate(
        self,
        level: LogLevel,
        stage: Stage,
        task: str,
        message: str,
    ) -> str: ...


class Plugin(Protocol):
    def register(self, registry: PluginRegistry) -> None: ...
