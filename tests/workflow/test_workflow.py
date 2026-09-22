from dataclasses import dataclass
from pathlib import Path

import pytest

from omniship import Logging, LogLevel, Pipeline
from omniship.plugins.github import (
    GitHubActions,
    GitHubPages,
    GitHubRelease,
    GitHubRunner,
    GitHubTag,
)
from omniship.plugins.packaging import Sha256Manifest, TarGz, Zip
from omniship.plugins.python import Pytest, Ruff, Wheel
from omniship.workflow.compiler import compile_pipeline
from omniship.workflow.errors import WorkflowError
from omniship.workflow.model import NodeSpec
from omniship.workflow.serializer import serialize_config


def generate_sources(ctx):
    pass


def run_tests(ctx):
    pass


def prepare_release(ctx):
    pass


def build_package(ctx):
    pass


def invalid_task():
    pass


@dataclass(frozen=True)
class ExampleRequirement:
    name: str
    version: str


@dataclass(frozen=True)
class RequirementBlock:
    requirements: tuple[ExampleRequirement, ...]
    name: str = "requirement-block"

    def compile(self, stage, workspace_root):
        return [
            NodeSpec(
                self.name,
                stage,
                "core/noop",
                requirements=self.requirements,
            )
        ]


def test_stage_functions_compile_typed_blocks(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        stage.task(Ruff())
        stage.task(Pytest(coverage=True, minimum_coverage=90))

    @pipeline.build
    def build(stage):
        stage.task(Wheel())

    @pipeline.ship
    def ship(stage):
        stage.task(GitHubRelease(repository="octacity-org/omniship", tag="v1.2.3"))

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    assert list(config.check) == ["ruff", "pytest"]
    assert config.check["pytest"].uses == "python/pytest"
    assert config.check["pytest"].with_ == {
        "coverage": True,
        "minimum_coverage": 90,
    }
    assert config.build["wheel"].uses == "python/wheel"
    assert config.ship["github-release"].with_["tag"] == "v1.2.3"


def test_github_pages_compiles_as_a_ship_block(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.ship
    def ship(stage):
        stage.task(GitHubPages(artifact="docs-site"))

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    assert config.ship["github-pages"].uses == "github/pages"
    assert config.ship["github-pages"].with_ == {"artifact": "docs-site"}


def test_github_tag_compiles_as_a_ship_block(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.ship
    def ship(stage):
        stage.task(GitHubTag(repository="octacity-org/omniship", tag="v1.2.3"))

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    assert config.ship["github-tag"].uses == "github/tag"
    assert config.ship["github-tag"].with_ == {
        "repository": "octacity-org/omniship",
        "tag": "v1.2.3",
    }


def test_packaging_blocks_compile_as_build_nodes(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.build
    def build(stage):
        archive = stage.task(
            TarGz(output="dist/demo.tar.gz", files={"build/demo": "demo"})
        )
        zipped = stage.task(
            Zip(output="dist/demo.zip", files={"build/demo": "demo"})
        )
        stage.task(
            Sha256Manifest(
                output="dist/SHA256SUMS.txt",
                artifacts=["demo-tar-gz", "demo-zip"],
            ),
            after=[archive, zipped],
        )

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    assert config.build["demo-tar-gz"].uses == "packaging/tar-gz"
    assert config.build["demo-zip"].uses == "packaging/zip"
    assert config.build["sha256-manifest"].uses == "packaging/sha256-manifest"


def test_github_pages_is_rejected_outside_ship(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.build
    def build(stage):
        stage.task(GitHubPages())

    with pytest.raises(WorkflowError, match="only be used in the ship stage"):
        compile_pipeline(pipeline, tmp_path / "workflow.py")


def test_pipeline_logging_configuration_is_compiled(tmp_path: Path) -> None:
    pipeline = Pipeline(
        logging=Logging(
            level=LogLevel.DEBUG,
            show_output=False,
            timestamps=True,
        )
    )

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")
    rendered = serialize_config(config, source_name="workflow.py")

    assert config.logging.level == LogLevel.DEBUG
    assert config.logging.show_output is False
    assert config.logging.timestamps is True
    assert "logging:" in rendered
    assert "level: debug" in rendered
    assert "show_output: false" in rendered
    assert "timestamps: true" in rendered


def test_nested_imperative_tasks_compile_with_dependencies(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        generate = stage.task(generate_sources, name="generate")
        stage.task(run_tests, name="test", after=[generate])

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    assert config.check["generate"].uses == "core/python"
    assert config.check["generate"].with_ == {
        "callable": "workflow.py:pipeline:check:generate"
    }
    assert config.check["test"].needs == ["generate"]


def test_execution_placement_applies_to_blocks_and_imperative_tasks(
    tmp_path: Path,
) -> None:
    github = GitHubActions(default_runner=GitHubRunner.UBUNTU_24_04)
    lint_job = github.job(runners=[GitHubRunner.UBUNTU_SLIM])
    test_job = github.job(runners=[GitHubRunner.UBUNTU_24_04, GitHubRunner.MACOS_15])
    pipeline = Pipeline(targets=[github])

    @pipeline.check
    def check(stage):
        stage.task(Ruff(), execution=lint_job)
        stage.task(run_tests, name="test", execution=test_job)

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    assert config.check["ruff"].execution is lint_job
    assert config.check["test"].execution is test_job
    assert "execution" not in serialize_config(config, source_name="workflow.py")


def test_requirements_apply_to_blocks_and_imperative_tasks(tmp_path: Path) -> None:
    python = ExampleRequirement("python/toolchain", "3.14")
    uv = ExampleRequirement("python/uv", "0.8")
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        stage.task(run_tests, name="test", requires=[python])
        stage.task(RequirementBlock((python,)), requires=[python, uv])

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    assert config.check["test"].requirements == (python,)
    assert config.check["requirement-block"].requirements == (python, uv)
    assert "requirements" not in serialize_config(config, source_name="workflow.py")


def test_conflicting_requirements_are_rejected(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        stage.task(
            run_tests,
            requires=[
                ExampleRequirement("python/toolchain", "3.13"),
                ExampleRequirement("python/toolchain", "3.14"),
            ],
        )

    with pytest.raises(WorkflowError, match="Conflicting requirement"):
        compile_pipeline(pipeline, tmp_path / "workflow.py")


def test_conflicting_block_and_task_requirements_are_rejected(
    tmp_path: Path,
) -> None:
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        stage.task(
            RequirementBlock((ExampleRequirement("python/toolchain", "3.13"),)),
            requires=[ExampleRequirement("python/toolchain", "3.14")],
        )

    with pytest.raises(WorkflowError, match="Conflicting requirement"):
        compile_pipeline(pipeline, tmp_path / "workflow.py")


def test_cross_stage_dependency_is_rejected(tmp_path: Path) -> None:
    pipeline = Pipeline()
    references = {}

    @pipeline.check
    def check(stage):
        references["prepare"] = stage.task(prepare_release, name="prepare")

    @pipeline.build
    def build(stage):
        stage.task(build_package, name="package", after=[references["prepare"]])

    with pytest.raises(WorkflowError, match="same stage"):
        compile_pipeline(pipeline, tmp_path / "workflow.py")


def test_invalid_task_signature_is_rejected_during_generation(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.build
    def build(stage):
        stage.task(invalid_task)

    with pytest.raises(WorkflowError, match="exactly one context"):
        compile_pipeline(pipeline, tmp_path / "workflow.py")


def test_duplicate_block_names_are_rejected(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        stage.task(Ruff())
        stage.task(Ruff())

    with pytest.raises(WorkflowError, match="Duplicate node name 'ruff'"):
        compile_pipeline(pipeline, tmp_path / "workflow.py")


def test_serializer_is_stable_and_uses_aliases(tmp_path: Path) -> None:
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        stage.task(Ruff())

    config = compile_pipeline(pipeline, tmp_path / "workflow.py")

    first = serialize_config(config, source_name="workflow.py")
    second = serialize_config(config, source_name="workflow.py")

    assert first == second
    assert first.startswith("# Generated by OmniShip from workflow.py")
    assert "\nversion: 1\n" in first
    assert "with_:" not in first
    assert first.endswith("\n")


def test_stage_definition_must_declare_at_least_one_task() -> None:
    pipeline = Pipeline()

    with pytest.raises(WorkflowError, match="did not declare any tasks"):

        @pipeline.check
        def check(stage):
            pass


def test_stage_can_only_be_defined_once() -> None:
    pipeline = Pipeline()

    @pipeline.check
    def first(stage):
        stage.task(Ruff())

    with pytest.raises(WorkflowError, match="already defined"):

        @pipeline.check
        def second(stage):
            stage.task(Pytest())


def test_direct_node_registration_is_not_a_competing_api() -> None:
    pipeline = Pipeline()

    with pytest.raises(WorkflowError, match="stage definition function"):
        pipeline.check(Ruff())
