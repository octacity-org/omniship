from collections.abc import Callable, Iterable
from typing import Any

from omniship.plugins.api import (
    LockUpdater,
    LogAnnotator,
    Operation,
    WorkflowGenerator,
)
from omniship.plugins.metadata import OperationDefinition


class RegistryError(Exception):
    pass


class PluginRegistry:
    def __init__(self) -> None:
        self._operations: dict[str, Operation] = {}
        self._definitions: dict[str, OperationDefinition] = {}
        self._workflow_generators: dict[str, WorkflowGenerator] = {}
        self._lock_updaters: dict[str, LockUpdater] = {}
        self._log_annotators: dict[str, LogAnnotator] = {}
        self._requirement_resolvers: dict[
            tuple[str, type[Any]], Callable[[Any], Any]
        ] = {}

    def register_operation(
        self,
        operation: Operation,
        definition: OperationDefinition | None = None,
    ) -> None:
        name = operation.name
        self._operations[name] = operation
        if definition is not None:
            self._definitions[name] = definition
        else:
            self._definitions[name] = OperationDefinition(
                name=name,
                stages=operation.stages,
                cacheable=getattr(operation, "cacheable", False),
            )

    def get_operation(self, name: str) -> Operation:
        if name not in self._operations:
            raise RegistryError(f"Operation '{name}' is not registered.")
        return self._operations[name]

    def get_definition(self, name: str) -> OperationDefinition | None:
        return self._definitions.get(name)

    def has_operation(self, name: str) -> bool:
        return name in self._operations

    def list_operations(self) -> list[OperationDefinition]:
        return list(self._definitions.values())

    def register_workflow_generator(self, generator: WorkflowGenerator) -> None:
        self._workflow_generators[generator.name] = generator

    def list_workflow_generators(self) -> list[WorkflowGenerator]:
        return list(self._workflow_generators.values())

    def register_lock_updater(self, updater: LockUpdater) -> None:
        self._lock_updaters[updater.name] = updater

    def list_lock_updaters(self) -> list[LockUpdater]:
        return list(self._lock_updaters.values())

    def register_log_annotator(self, annotator: LogAnnotator) -> None:
        self._log_annotators[annotator.name] = annotator

    def list_log_annotators(self) -> list[LogAnnotator]:
        return list(self._log_annotators.values())

    def register_requirement_resolver(
        self,
        target: str,
        requirement_type: type[Any],
        resolver: Callable[[Any], Any],
    ) -> None:
        """Register how one target provisions a typed requirement."""

        key = (target, requirement_type)
        if key in self._requirement_resolvers:
            raise RegistryError(
                f"Requirement resolver for '{target}' and "
                f"'{requirement_type.__name__}' is already registered"
            )
        self._requirement_resolvers[key] = resolver

    def resolve_requirements(
        self,
        target: str,
        requirements: Iterable[Any],
    ) -> tuple[Any, ...]:
        """Resolve typed requirements into target-owned declarations."""

        resolved: list[Any] = []
        for requirement in requirements:
            resolver = self._requirement_resolvers.get((target, type(requirement)))
            if resolver is None:
                raise RegistryError(
                    f"Target '{target}' cannot provision requirement "
                    f"'{getattr(requirement, 'name', type(requirement).__name__)}'"
                )
            resolved.append(resolver(requirement))
        return tuple(resolved)
