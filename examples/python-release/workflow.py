from omniship import Pipeline
from omniship.plugins.github import GitHubRelease
from omniship.plugins.python import Pytest, Python, Ruff

pipeline = Pipeline()


@pipeline.check
def check(stage):
    stage.task(Ruff())
    stage.task(Pytest())


@pipeline.build
def build(stage):
    @stage.task
    def package(ctx):
        wheel = Python(ctx).build_wheel()
        ctx.artifacts.add(wheel)


@pipeline.ship
def ship(stage):
    stage.task(
        GitHubRelease(
            repository="octacity-org/omniship",
            tag="v0.1.0",
            notes="auto",
            dry_run=True,
        )
    )
