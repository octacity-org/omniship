"""Official GitLab CI provider."""

from omniship.plugins.registry import PluginRegistry

from .ci import DEFAULT_IMAGE, GitLabCI, GitLabCIGenerator, GitLabJob


def register_gitlab_plugin(registry: PluginRegistry) -> None:
    registry.register_workflow_generator(GitLabCIGenerator(registry))


__all__ = [
    "DEFAULT_IMAGE",
    "GitLabCI",
    "GitLabJob",
    "register_gitlab_plugin",
]
