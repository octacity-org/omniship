from omniship.core.logging import Logging, LogLevel
from omniship.workflow import (
    GitPluginPackage,
    Pipeline,
    PluginIndex,
    PluginPackage,
    StageBuilder,
)


def main() -> None:
    print("Hello from omniship!")


__all__ = [
    "GitPluginPackage",
    "LogLevel",
    "Logging",
    "Pipeline",
    "PluginIndex",
    "PluginPackage",
    "StageBuilder",
]
