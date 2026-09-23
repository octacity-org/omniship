from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from omniship.core.execution import SecretRef
from omniship.core.logging import Logging
from omniship.core.requirement import Requirement
from omniship.core.stage import Stage

from .errors import WorkflowError


@dataclass(frozen=True)
class NodeRef:
    name: str
    stage: Stage


@dataclass(frozen=True, slots=True)
class PluginPackage:
    """An exactly versioned plugin distribution needed by generated CI jobs."""

    name: str
    version: str
    index: PluginIndex | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", self.name)
            is None
        ):
            raise ValueError("Plugin package name must be a distribution name")
        if (
            not isinstance(self.version, str)
            or re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]*", self.version) is None
        ):
            raise ValueError("Plugin package version must be exact")
        if self.index is not None and not isinstance(self.index, PluginIndex):
            raise TypeError("Plugin package index must be a PluginIndex")

    @property
    def requirement(self) -> str:
        return f"{self.name}=={self.version}"


def _plugin_source_url(url: str) -> None:
    if not isinstance(url, str):
        raise ValueError("Plugin source must use an HTTPS URL")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise ValueError("Plugin source URL must not contain whitespace or controls")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Plugin source must use an HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Plugin source URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Plugin source URL must not contain a query or fragment")


@dataclass(frozen=True, slots=True)
class PluginIndex:
    """Named private package index authenticated by a CI-managed secret."""

    name: str
    url: str
    password: SecretRef
    username: str = "__token__"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", self.name) is None
        ):
            raise ValueError(
                "Plugin index name must contain letters, digits, '_' or '-'"
            )
        _plugin_source_url(self.url)
        if not isinstance(self.password, SecretRef):
            raise TypeError("Plugin index password must be a SecretRef")
        if (
            not isinstance(self.username, str)
            or not self.username
            or any(c in self.username for c in "\r\n")
        ):
            raise ValueError("Plugin index username must be a non-empty single line")

    @property
    def env_prefix(self) -> str:
        return "UV_INDEX_" + re.sub(r"[^A-Za-z0-9]", "_", self.name).upper()


@dataclass(frozen=True, slots=True)
class GitPluginPackage:
    """Git plugin selected by a commit or version tag; CI uses a locked SHA."""

    name: str
    repository: str
    commit: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", self.name)
            is None
        ):
            raise ValueError("Plugin package name must be a distribution name")
        _plugin_source_url(self.repository)
        if self.commit is None and self.version is None:
            raise ValueError("Git plugin requires a commit or version tag")
        if self.commit is not None and (
            not isinstance(self.commit, str)
            or re.fullmatch(r"[0-9a-fA-F]{40}", self.commit) is None
        ):
            raise ValueError("Git plugin commit must use a full 40-character SHA")
        if self.version is not None and (
            not isinstance(self.version, str)
            or re.fullmatch(
                r"v?[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?",
                self.version,
            )
            is None
        ):
            raise ValueError("Git plugin version must be an exact version tag")

    @property
    def requirement(self) -> str:
        if self.commit is None:
            raise ValueError("Git plugin version must be resolved to a commit")
        return f"{self.name} @ git+{self.repository}@{self.commit}"


@dataclass(frozen=True)
class NodeSpec:
    name: str
    stage: Stage
    uses: str
    params: dict[str, Any] = field(default_factory=dict)
    needs: tuple[NodeRef, ...] = ()
    condition: dict[str, Any] | str | None = None
    execution: Any = None
    requirements: tuple[Requirement, ...] = ()


class Block(Protocol):
    def compile(self, stage: Stage, workspace_root: Any) -> list[NodeSpec]: ...


@dataclass(frozen=True)
class TaskDeclaration:
    function: Callable[[Any], Any]
    ref: NodeRef
    after: tuple[NodeRef, ...] = ()
    execution: Any = None
    requirements: tuple[Requirement, ...] = ()


@dataclass(frozen=True)
class BlockDeclaration:
    block: Block
    after: tuple[NodeRef, ...] = ()
    execution: Any = None
    requirements: tuple[Requirement, ...] = ()


class StageBuilder:
    def __init__(self, pipeline: Pipeline, stage: Stage) -> None:
        self._pipeline = pipeline
        self.stage = stage

    def task(
        self,
        item: Any = None,
        *,
        after: Iterable[NodeRef | Callable[[Any], Any]] = (),
        name: str | None = None,
        execution: Any = None,
        requires: Iterable[Requirement] = (),
    ) -> Any:
        if item is None:

            def decorator(function: Callable[[Any], Any]) -> Callable[[Any], Any]:
                return self._pipeline._add_imperative_task(
                    self.stage,
                    function,
                    name=name,
                    after=after,
                    execution=execution,
                    requirements=requires,
                )

            return decorator
        if inspect.isfunction(item):
            return self._pipeline._add_imperative_task(
                self.stage,
                item,
                name=name,
                after=after,
                execution=execution,
                requirements=requires,
            )
        if name is not None:
            raise WorkflowError("name applies only to imperative tasks")
        return self._pipeline._add_block_task(
            self.stage,
            item,
            after=after,
            execution=execution,
            requirements=requires,
        )


class Pipeline:
    def __init__(
        self,
        *,
        targets: Iterable[Any] = (),
        plugins: Iterable[PluginPackage | GitPluginPackage] = (),
        logging: Logging | None = None,
    ) -> None:
        self._entries: dict[Stage, list[BlockDeclaration | TaskDeclaration]] = {
            stage: [] for stage in Stage
        }
        self._stage_definitions: dict[Stage, Callable[[StageBuilder], Any]] = {}
        self._task_refs: dict[Callable[[Any], Any], NodeRef] = {}
        self._task_callables: dict[tuple[Stage, str], Callable[[Any], Any]] = {}
        self.targets = tuple(targets)
        self.plugins = tuple(plugins)
        if any(
            not isinstance(plugin, (PluginPackage, GitPluginPackage))
            for plugin in self.plugins
        ):
            raise TypeError(
                "plugins must contain PluginPackage or GitPluginPackage values"
            )
        normalized = [
            re.sub(r"[-_.]+", "-", plugin.name).lower() for plugin in self.plugins
        ]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Pipeline contains duplicate plugin packages")
        if logging is not None and not isinstance(logging, Logging):
            raise TypeError("logging must be a Logging value")
        self.logging = logging or Logging()

    def check(
        self, definition: Callable[[StageBuilder], Any]
    ) -> Callable[[StageBuilder], Any]:
        return self._define_stage(Stage.CHECK, definition)

    def build(
        self, definition: Callable[[StageBuilder], Any]
    ) -> Callable[[StageBuilder], Any]:
        return self._define_stage(Stage.BUILD, definition)

    def ship(
        self, definition: Callable[[StageBuilder], Any]
    ) -> Callable[[StageBuilder], Any]:
        return self._define_stage(Stage.SHIP, definition)

    def entries(self, stage: Stage) -> tuple[BlockDeclaration | TaskDeclaration, ...]:
        return tuple(self._entries[stage])

    def task_callable(self, stage: Stage, name: str) -> Callable[[Any], Any]:
        try:
            return self._task_callables[(stage, name)]
        except KeyError as exc:
            raise WorkflowError(
                f"Imperative task '{name}' does not exist in stage '{stage.value}'"
            ) from exc

    def _define_stage(
        self,
        stage: Stage,
        definition: Callable[[StageBuilder], Any],
    ) -> Callable[[StageBuilder], Any]:
        if not inspect.isfunction(definition):
            raise WorkflowError(
                f"pipeline.{stage.value} requires a stage definition function"
            )
        if stage in self._stage_definitions:
            raise WorkflowError(f"Stage '{stage.value}' is already defined")
        if len(inspect.signature(definition).parameters) != 1:
            raise WorkflowError(
                f"Stage definition '{definition.__name__}' must accept exactly one stage argument"
            )

        self._stage_definitions[stage] = definition
        before = len(self._entries[stage])
        definition(StageBuilder(self, stage))
        if len(self._entries[stage]) == before:
            self._stage_definitions.pop(stage, None)
            raise WorkflowError(
                f"{stage.value.capitalize()} stage '{definition.__name__}' did not declare any tasks"
            )
        return definition

    def _add_imperative_task(
        self,
        stage: Stage,
        function: Callable[[Any], Any],
        *,
        name: str | None,
        after: Iterable[NodeRef | Callable[[Any], Any]],
        execution: Any,
        requirements: Iterable[Requirement],
    ) -> Callable[[Any], Any]:
        ref = NodeRef(name or function.__name__.replace("_", "-"), stage)
        after_refs = tuple(self._resolve_ref(item) for item in after)
        self._entries[stage].append(
            TaskDeclaration(function, ref, after_refs, execution, tuple(requirements))
        )
        self._task_refs[function] = ref
        self._task_callables[(stage, ref.name)] = function
        return function

    def _add_block_task(
        self,
        stage: Stage,
        block: Block,
        *,
        after: Iterable[NodeRef | Callable[[Any], Any]],
        execution: Any,
        requirements: Iterable[Requirement],
    ) -> NodeRef:
        ref = NodeRef(self._block_name(block), stage)
        after_refs = tuple(self._resolve_ref(item) for item in after)
        self._entries[stage].append(
            BlockDeclaration(block, after_refs, execution, tuple(requirements))
        )
        return ref

    def _resolve_ref(self, value: NodeRef | Callable[[Any], Any]) -> NodeRef:
        if isinstance(value, NodeRef):
            return value
        try:
            return self._task_refs[value]
        except (KeyError, TypeError) as exc:
            raise WorkflowError(
                "after must reference a registered task or node"
            ) from exc

    @staticmethod
    def _block_name(block: Any) -> str:
        block_name = getattr(block, "name", None)
        if not isinstance(block_name, str) or not block_name:
            raise WorkflowError(f"{type(block).__name__} is not a valid workflow block")
        return block_name
