from omniship.core.execution import Platform
from omniship.core.logging import Logging, LogLevel
from omniship.workflow import (
    Command,
    GitPluginPackage,
    Pipeline,
    PluginIndex,
    PluginPackage,
    StageBuilder,
    Steps,
)


def main() -> None:
    print("Hello from omniship!")


__all__ = [
    "Command",
    "GitPluginPackage",
    "LogLevel",
    "Logging",
    "Pipeline",
    "Platform",
    "PluginIndex",
    "PluginPackage",
    "StageBuilder",
    "Steps",
]
