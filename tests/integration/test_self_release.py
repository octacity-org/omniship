import tomllib
from pathlib import Path

import yaml
from click.testing import CliRunner

from omniship.cli.app import cli
from omniship.workflow import compile_pipeline, load_workflow


def test_omniship_self_release_is_generated_and_current() -> None:
    root = Path(__file__).parents[2]
    workflow_path = root / "workflow.py"
    config_path = root / "omniship.yaml"
    workflow_root = root / ".github" / "workflows"
    check_path = workflow_root / "check.yml"
    build_path = workflow_root / "build.yml"
    ship_path = workflow_root / "ship.yml"

    assert workflow_path.is_file()
    pipeline = load_workflow(workflow_path)
    config = compile_pipeline(pipeline, workflow_path)
    publish = config.ship["pypi-publish"]
    release = config.ship["github-release"].with_
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert publish.with_["trusted_publishing"] is True
    assert publish.execution.environment == "release"
    assert config.ship["github-release"].needs == ["pypi-publish"]
    assert release["repository"] == "octacity-org/omniship"
    assert release["tag"] == f"v{project['project']['version']}"
    assert release.get("dry_run") is None
    assert "tag=" not in workflow_path.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "generate",
            "--workflow-file",
            str(workflow_path),
            "--output",
            str(config_path),
            "--check",
        ],
    )

    assert result.exit_code == 0, result.output
    assert check_path.is_file()
    assert build_path.is_file()
    assert ship_path.is_file()
    actions = yaml.load(
        ship_path.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert actions["on"]["push"]["tags"] == ["v*"]
    publish_job = actions["jobs"]["ship-pypi-publish"]
    release_job = actions["jobs"]["ship-github-release"]
    assert publish_job["environment"] == "release"
    assert publish_job["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert release_job["needs"] == "ship-pypi-publish"
