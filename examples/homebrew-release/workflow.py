"""Example for an existing acme/demo project and acme/tools tap.

The runner supplies brew; configure HOMEBREW_TOKEN with tap write/PR access.
The example archive is built for Apple Silicon, so its formula must target that
platform. Adapt the archive and formula to your own supported platforms.
"""

from omniship import Pipeline
from omniship.core.execution import SecretRef
from omniship.plugins.github import (
    GitHub,
    GitHubActions,
    GitHubPermission,
    GitHubPermissions,
    GitHubRunner,
)
from omniship.plugins.homebrew import Homebrew, HomebrewTap
from omniship.plugins.packaging import Archive
from omniship.plugins.rust import CargoTest, Rust, RustToolchain

github = GitHubActions(default_runner=GitHubRunner.MACOS_15)
pipeline = Pipeline(targets=[github])
rust = RustToolchain(toolchain="1.85.0")


@pipeline.check
def check(stage):
    stage.task(CargoTest(), requires=[rust])


@pipeline.build
def build(stage):
    @stage.task(requires=[rust])
    def package(ctx):
        Rust(ctx).build(release=True)
        Archive(ctx).tar_gz(
            output="dist/demo.tar.gz", files={"target/release/demo": "demo"}
        )


@pipeline.ship
def ship(stage):
    @stage.task(
        name="github-release",
        execution=github.job(
            permissions=GitHubPermissions(contents=GitHubPermission.WRITE),
            env={"GITHUB_TOKEN": SecretRef("GITHUB_TOKEN")},
        ),
    )
    def release(ctx):
        GitHub(ctx).release(repository="acme/demo", tag=ctx.env["GITHUB_REF_NAME"])

    @stage.task(
        after=[release],
        requires=[HomebrewTap("acme/tools")],
        execution=github.job(
            env={"HOMEBREW_GITHUB_API_TOKEN": SecretRef("HOMEBREW_TOKEN")}
        ),
    )
    def update_tap(ctx):
        tag = ctx.env["GITHUB_REF_NAME"]
        Homebrew(ctx).formula(
            formula="acme/tools/demo",
            artifact="demo.tar.gz",
            version=tag.removeprefix("v"),
            url=f"https://github.com/acme/demo/releases/download/{tag}/demo.tar.gz",
        )
