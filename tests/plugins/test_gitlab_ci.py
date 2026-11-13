import ast
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from omniship.cli.app import cli
from omniship.config.models import NodeConfig, OmniShipConfig
from omniship.core.execution import IdentityToken
from omniship.plugins.github import GitHubActions
from omniship.plugins.gitlab import GitLabCI, GitLabJob, register_gitlab_plugin
from omniship.plugins.gitlab.ci import GitLabCIGenerator
from omniship.plugins.registry import PluginRegistry, RegistryError
from omniship.workflow.model import (
    GitPluginPackage,
    Pipeline,
    PluginIndex,
    PluginPackage,
)

GOLDEN = Path(__file__).parent / "golden" / "gitlab_ci.yml"
COMMIT = "a" * 40


def _generate(
    tmp_path: Path,
    config: OmniShipConfig,
    pipeline: Pipeline,
    *,
    omniship_version: str | None = "9.9.9",
    registry: PluginRegistry | None = None,
) -> str:
    if omniship_version is not None:
        (tmp_path / "omniship.lock").write_text(
            f'version = 1\n\n[omniship]\nversion = "{omniship_version}"\n',
            encoding="utf-8",
        )
    generated = GitLabCIGenerator(registry or PluginRegistry()).generate(
        config,
        tmp_path / "workflow.py",
        tmp_path / "omniship.yaml",
        pipeline,
    )
    assert [item.path for item in generated] == [tmp_path / ".gitlab-ci.yml"]
    return generated[0].content


def _document(tmp_path: Path, config: OmniShipConfig, pipeline: Pipeline) -> dict:
    return yaml.safe_load(_generate(tmp_path, config, pipeline))


def _golden_config(gitlab: GitLabCI) -> OmniShipConfig:
    """lint -> test and an independent types check, then package -> docs."""

    return OmniShipConfig(
        version=1,
        check={
            "lint": NodeConfig(uses="core/noop"),
            "test": NodeConfig(
                uses="core/noop",
                needs=["lint"],
                execution=gitlab.job(
                    image="ghcr.io/astral-sh/uv:0.13.8-python3.14-trixie",
                    tags=["linux", "large"],
                    timeout_minutes=20,
                    variables={
                        "LOG_LEVEL": "debug",
                        "PRICE": "5$",
                        "TOKEN": GitLabCI.secret("PYPI_TOKEN"),
                        "PYPI_TOKEN": GitLabCI.secret("PYPI_TOKEN"),
                    },
                ),
            ),
            "types": NodeConfig(uses="core/noop"),
        },
        build={
            "package": NodeConfig(uses="core/noop"),
            "docs": NodeConfig(uses="core/noop", needs=["package"]),
        },
    )


def test_gitlab_ci_matches_the_golden_file(tmp_path: Path) -> None:
    gitlab = GitLabCI()

    content = _generate(tmp_path, _golden_config(gitlab), Pipeline(targets=[gitlab]))

    assert content == GOLDEN.read_text(encoding="utf-8")


def test_golden_file_encodes_stage_barriers_and_failure_isolation() -> None:
    """The needs graph GitLab enforces when a job fails.

    GitLab runs a job only after every job it ``needs`` succeeded. So a
    failing ``check-lint`` stops ``check-test`` and ``check-complete``, while
    ``check-types`` (which needs nothing) still runs. Build roots need
    ``check-complete``, so no Build job starts after any Check failure.
    """

    jobs = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))

    assert "needs" not in jobs["check-lint"]
    assert "needs" not in jobs["check-types"]
    assert jobs["check-test"]["needs"] == ["check-lint"]
    assert jobs["check-complete"]["needs"] == ["check-test", "check-types"]
    assert jobs["build-package"]["needs"] == ["check-complete"]
    assert jobs["build-docs"]["needs"] == ["build-package"]
    assert jobs["build-complete"]["needs"] == ["build-docs"]
    assert jobs["stages"] == ["check", "build", "ship"]


def _validate_gitlab_schema(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "check_jsonschema",
            "--builtin-schema",
            "vendor.gitlab-ci",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_golden_file_satisfies_the_gitlab_ci_schema() -> None:
    result = _validate_gitlab_schema(GOLDEN)

    assert result.returncode == 0, result.stdout + result.stderr


def test_gitlab_ci_schema_check_rejects_invalid_keywords(tmp_path: Path) -> None:
    document = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    document["check-lint"]["needz"] = []
    invalid = tmp_path / ".gitlab-ci.yml"
    invalid.write_text(yaml.safe_dump(document), encoding="utf-8")

    result = _validate_gitlab_schema(invalid)

    assert result.returncode != 0


def test_generator_ignores_pipelines_without_a_gitlab_target(tmp_path: Path) -> None:
    config = OmniShipConfig(version=1, check={"lint": NodeConfig(uses="core/noop")})

    generated = GitLabCIGenerator(PluginRegistry()).generate(
        config,
        tmp_path / "workflow.py",
        tmp_path / "omniship.yaml",
        Pipeline(targets=[GitHubActions()]),
    )

    assert generated == ()


def test_jobs_use_their_gitlab_placement_among_other_targets(tmp_path: Path) -> None:
    gitlab = GitLabCI()
    github = GitHubActions()
    config = OmniShipConfig(
        version=1,
        check={
            "placed": NodeConfig(
                uses="core/noop",
                execution=(github.job(), gitlab.job(tags=["arm64"])),
            ),
            "defaulted": NodeConfig(uses="core/noop", execution=github.job()),
        },
    )

    document = _document(tmp_path, config, Pipeline(targets=[github, gitlab]))

    assert document["check-placed"]["tags"] == ["arm64"]
    assert "tags" not in document["check-defaulted"]


def test_jobs_run_the_locked_omniship_with_pipeline_plugins(tmp_path: Path) -> None:
    gitlab = GitLabCI()
    (tmp_path / "omniship.lock").write_text(
        "\n".join(
            [
                "version = 1",
                "[omniship]",
                'version = "1.2.3"',
                "[[plugin_sources]]",
                'type = "git"',
                'name = "omniship-git"',
                'repository = "https://example.com/acme/plugin.git"',
                f'commit = "{COMMIT}"',
                'version = "v1.0.0"',
            ]
        ),
        encoding="utf-8",
    )
    pipeline = Pipeline(
        targets=[gitlab],
        plugins=[
            PluginPackage("omniship-acme", "2.0.0"),
            GitPluginPackage(
                "omniship-git",
                "https://example.com/acme/plugin.git",
                version="v1.0.0",
            ),
        ],
    )
    config = OmniShipConfig(version=1, check={"lint": NodeConfig(uses="core/noop")})

    document = yaml.safe_load(
        _generate(tmp_path, config, pipeline, omniship_version=None)
    )

    assert document["check-lint"]["script"] == [
        "uvx --from omniship==1.2.3 --with omniship-acme==2.0.0 "
        "--with 'omniship-git @ git+https://example.com/acme/plugin.git@"
        f"{COMMIT}' omniship run-node --stage check --node lint "
        "--config omniship.yaml"
    ]


@pytest.mark.parametrize(
    ("plugin", "message"),
    [
        (
            PluginPackage(
                "omniship-private",
                "1.0.0",
                index=PluginIndex(
                    "acme",
                    "https://packages.example.com/simple",
                    password=GitLabCI.secret("ACME_TOKEN"),
                ),
            ),
            "private index",
        ),
        (
            GitPluginPackage(
                "omniship-git",
                "https://example.com/acme/plugin.git",
                version="v1.0.0",
            ),
            "has no locked commit",
        ),
    ],
)
def test_unsupported_plugin_sources_fail_generation(
    tmp_path: Path, plugin: PluginPackage | GitPluginPackage, message: str
) -> None:
    config = OmniShipConfig(version=1, check={"lint": NodeConfig(uses="core/noop")})

    with pytest.raises(ValueError, match=message):
        _generate(tmp_path, config, Pipeline(targets=[GitLabCI()], plugins=[plugin]))


def test_ship_tasks_are_rejected_until_artifact_handoff_exists(
    tmp_path: Path,
) -> None:
    config = OmniShipConfig(version=1, ship={"publish": NodeConfig(uses="core/noop")})

    with pytest.raises(ValueError, match="cannot generate Ship tasks yet"):
        _generate(tmp_path, config, Pipeline(targets=[GitLabCI()]))


def test_unprovisioned_requirements_fail_generation(tmp_path: Path) -> None:
    config = OmniShipConfig(
        version=1,
        check={
            "publish": NodeConfig(uses="core/noop", requirements=(IdentityToken(),))
        },
    )

    with pytest.raises(
        RegistryError,
        match="Target 'gitlab/ci' cannot provision requirement 'core/identity-token'",
    ):
        _generate(tmp_path, config, Pipeline(targets=[GitLabCI()]))


def test_job_names_cannot_collide_with_the_stage_gate(tmp_path: Path) -> None:
    config = OmniShipConfig(version=1, check={"complete": NodeConfig(uses="core/noop")})

    with pytest.raises(ValueError, match="job name collision"):
        _generate(tmp_path, config, Pipeline(targets=[GitLabCI()]))


def test_foreign_placements_owned_by_gitlab_are_rejected(tmp_path: Path) -> None:
    class Impostor:
        target = "gitlab/ci"

    config = OmniShipConfig(
        version=1,
        check={"lint": NodeConfig(uses="core/noop", execution=Impostor())},
    )

    with pytest.raises(ValueError, match="not a GitLab job"):
        _generate(tmp_path, config, Pipeline(targets=[GitLabCI()]))


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"image": ""}, TypeError),
        ({"tags": "linux"}, TypeError),
        ({"tags": ("linux", "linux")}, ValueError),
        ({"timeout_minutes": 0}, ValueError),
        ({"variables": {"1BAD": "x"}}, ValueError),
        ({"variables": {"OMNISHIP_REVISION": "x"}}, ValueError),
        ({"variables": {"COUNT": 3}}, TypeError),
    ],
)
def test_gitlab_job_rejects_invalid_placement(arguments: dict, error: type) -> None:
    with pytest.raises(error):
        GitLabJob(**arguments)


def test_gitlab_plugin_registers_only_its_generator() -> None:
    registry = PluginRegistry()

    register_gitlab_plugin(registry)

    assert [g.name for g in registry.list_workflow_generators()] == ["gitlab/ci"]
    assert registry.list_operations() == []
    assert registry.list_lock_updaters() == []
    assert registry.list_log_annotators() == []


def test_gitlab_plugin_does_not_depend_on_the_github_plugin() -> None:
    package = Path(__file__).resolve().parents[2] / "src/omniship/plugins/gitlab"
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("omniship.plugins.github"), path
            if isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith("omniship.plugins.github")
                    for alias in node.names
                ), path


def test_generate_writes_both_targets_from_one_workflow(tmp_path: Path) -> None:
    source = tmp_path / "workflow.py"
    output = tmp_path / "omniship.yaml"
    source.write_text(
        "from omniship import Pipeline\n"
        "from omniship.plugins.github import GitHubActions\n"
        "from omniship.plugins.gitlab import GitLabCI\n"
        "gitlab = GitLabCI()\n"
        "pipeline = Pipeline(targets=[GitHubActions(), gitlab])\n"
        "@pipeline.check\n"
        "def check(stage):\n"
        "    @stage.task(execution=gitlab.job(tags=['linux']))\n"
        "    def verify(ctx):\n"
        "        pass\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    generated = runner.invoke(cli, ["generate", "-f", str(source), "-o", str(output)])
    checked = runner.invoke(
        cli, ["generate", "-f", str(source), "-o", str(output), "--check"]
    )

    assert generated.exit_code == 0, generated.output
    assert checked.exit_code == 0, checked.output
    assert (tmp_path / ".github" / "workflows" / "check.yml").is_file()
    document = yaml.safe_load((tmp_path / ".gitlab-ci.yml").read_text("utf-8"))
    assert document["check-verify"]["tags"] == ["linux"]
    assert _validate_gitlab_schema(tmp_path / ".gitlab-ci.yml").returncode == 0
