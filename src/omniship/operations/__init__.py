from omniship.operations.command import CommandOperation, get_command_definition
from omniship.operations.noop import NoopOperation, get_noop_definition
from omniship.operations.python_task import (
    PythonTaskOperation,
    get_python_task_definition,
)
from omniship.operations.steps import StepsOperation, get_steps_definition
from omniship.plugins.registry import PluginRegistry


def register_core_plugin(registry: PluginRegistry) -> None:
    registry.register_operation(
        CommandOperation(),
        get_command_definition(),
    )
    registry.register_operation(
        NoopOperation(),
        get_noop_definition(),
    )
    registry.register_operation(PythonTaskOperation(), get_python_task_definition())
    registry.register_operation(StepsOperation(registry), get_steps_definition())
