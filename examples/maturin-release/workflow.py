"""ABI3 wheel release; Cargo.toml/pyproject.toml supply the package version."""

from omniship import Pipeline
from omniship.plugins.github import GitHubActions, GitHubRunner
from omniship.plugins.maturin import MaturinToolchain, MaturinWheel
from omniship.plugins.python import PyPIPublish
from omniship.plugins.rust import CargoTest, RustToolchain

github = GitHubActions()
pipeline = Pipeline(targets=[github])
rust = RustToolchain(toolchain="1.85.0")


@pipeline.check
def check(stage):
    stage.task(CargoTest(), requires=[rust])


@pipeline.build
def build(stage):
    for target, runner in [
        ("aarch64-apple-darwin", GitHubRunner.MACOS_15),
        ("x86_64-pc-windows-msvc", GitHubRunner.WINDOWS_2025),
    ]:
        stage.task(
            MaturinWheel(
                target=target, features=("pyo3/abi3-py39",), name=f"wheel-{target}"
            ),
            requires=[
                RustToolchain(toolchain="1.85.0", targets=[target]),
                MaturinToolchain(version="1.9.4"),
            ],
            execution=github.job(runners=[runner]),
        )


@pipeline.ship
def ship(stage):
    stage.task(
        PyPIPublish(trusted_publishing=True),
        execution=github.job(environment="release"),
    )
