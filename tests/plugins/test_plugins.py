from dataclasses import dataclass

import pytest

from omniship.core.stage import Stage
from omniship.plugins.discovery import load_plugins
from omniship.plugins.registry import PluginRegistry, RegistryError
from omniship.plugins.template import generate_operation_template


def test_plugin_registry_and_discovery():
    reg = load_plugins()
    assert reg.has_operation("core/command")
    assert reg.has_operation("core/noop")
    assert reg.has_operation("core/python")
    assert reg.has_operation("python/ruff")
    assert reg.has_operation("python/pytest")
    assert reg.has_operation("python/wheel")
    assert reg.has_operation("github/release")
    assert [generator.name for generator in reg.list_workflow_generators()] == [
        "github/actions",
        "gitlab/ci",
    ]

    op = reg.get_operation("core/command")
    assert op.name == "core/command"
    assert Stage.BUILD in op.stages


def test_operation_template_generation():
    reg = load_plugins()
    cmd_def = reg.get_definition("core/command")
    assert cmd_def is not None

    template = generate_operation_template(cmd_def)
    assert "uses: core/command" in template
    assert "run:" in template
    assert "artifacts:" in template


def test_custom_plugin_registration():
    reg = PluginRegistry()

    class CustomOp:
        name = "custom/test"
        stages = frozenset({Stage.CHECK})
        cacheable = True

        async def execute(self, context, inputs):
            pass

    def custom_plugin(r: PluginRegistry):
        r.register_operation(CustomOp())

    custom_plugin(reg)
    assert reg.has_operation("custom/test")
    definition = reg.get_definition("custom/test")
    assert definition.name == "custom/test"
    assert definition.cacheable is True


def test_custom_plugin_can_register_a_workflow_generator():
    reg = PluginRegistry()

    class CustomGenerator:
        name = "custom/ci"

        def generate(self, config, source_path, config_path, pipeline):
            return ()

    generator = CustomGenerator()
    reg.register_workflow_generator(generator)

    assert reg.list_workflow_generators() == [generator]


def test_plugins_can_register_target_specific_requirement_resolvers() -> None:
    @dataclass(frozen=True)
    class Toolchain:
        version: str
        name: str = "example/toolchain"

    registry = PluginRegistry()
    registry.register_requirement_resolver(
        "github/actions",
        Toolchain,
        lambda requirement: {
            "uses": "example/setup@sha",
            "with": {"version": requirement.version},
        },
    )

    rendered = registry.resolve_requirements(
        "github/actions",
        [Toolchain("1.2.3")],
    )

    assert rendered == (
        {
            "uses": "example/setup@sha",
            "with": {"version": "1.2.3"},
        },
    )


def test_missing_or_duplicate_requirement_resolvers_are_rejected() -> None:
    @dataclass(frozen=True)
    class Toolchain:
        name: str = "example/toolchain"

    registry = PluginRegistry()
    registry.register_requirement_resolver(
        "github/actions", Toolchain, lambda requirement: requirement.name
    )

    with pytest.raises(RegistryError, match="already registered"):
        registry.register_requirement_resolver(
            "github/actions", Toolchain, lambda requirement: requirement.name
        )
    with pytest.raises(RegistryError, match="cannot provision"):
        registry.resolve_requirements("other/target", [Toolchain()])
