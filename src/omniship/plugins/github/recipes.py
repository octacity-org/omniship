"""Release recipes that ship through the official GitHub provider."""

from omniship.plugins.github import GitHubRelease
from omniship.plugins.python import Pytest, Ruff, Wheel
from omniship.workflow.model import Pipeline


def PythonRelease(
    *,
    repository: str | None = None,
    tag: str | None = None,
    coverage: bool = False,
    minimum_coverage: int | None = None,
    verify_wheel: bool = True,
    dry_run: bool = False,
) -> Pipeline:
    pipeline = Pipeline()

    @pipeline.check
    def check(stage):
        stage.task(Ruff())
        stage.task(Pytest(coverage=coverage, minimum_coverage=minimum_coverage))

    @pipeline.build
    def build(stage):
        stage.task(Wheel(verify=verify_wheel))

    @pipeline.ship
    def ship(stage):
        stage.task(
            GitHubRelease(
                repository=repository,
                tag=tag,
                notes="auto",
                dry_run=dry_run,
            )
        )

    return pipeline
