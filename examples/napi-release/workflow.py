"""Native npm release; package.json owns the package version and napi.targets."""

from omniship import Pipeline
from omniship.plugins.github import GitHubActions, GitHubRunner
from omniship.plugins.napi import NapiBuild, NapiPackage, NapiToolchain
from omniship.plugins.node import NodeTest, NodeToolchain, NpmPublish
from omniship.plugins.rust import RustToolchain

github = GitHubActions()
pipeline = Pipeline(targets=[github])
node = NodeToolchain(version="24.8.0")
napi = NapiToolchain(version="3.0.0")


@pipeline.check
def check(stage):
    stage.task(NodeTest(), requires=[node])


@pipeline.build
def build(stage):
    builds = []
    names = []
    for target, runner in [
        ("x86_64-unknown-linux-gnu", GitHubRunner.UBUNTU_24_04),
        ("aarch64-apple-darwin", GitHubRunner.MACOS_15),
    ]:
        builds.append(
            stage.task(
                NapiBuild(target=target, name=f"native-{target}"),
                requires=[
                    node,
                    RustToolchain(toolchain="1.85.0", targets=[target]),
                    napi,
                ],
                execution=github.job(runners=[runner]),
            )
        )
        names.append(f"napi-{target}")
    stage.task(NapiPackage(artifacts=tuple(names)), after=builds, requires=[node, napi])


@pipeline.ship
def ship(stage):
    stage.task(
        NpmPublish(from_artifacts=True, trusted_publishing=True), requires=[node]
    )
