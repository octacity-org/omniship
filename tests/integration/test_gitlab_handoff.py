"""Run generated GitLab jobs locally, moving artifacts the way GitLab does.

GitLab uploads each Build job's ``artifacts:paths`` and restores them at the
same relative path in every job that ``needs`` the producer. Restored files
may lose their mode bits, so the simulation clears them.
"""

import os
import shlex
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result

from omniship.cli.app import cli
from omniship.config.loader import load_config_from_file
from omniship.core.artifact_bundle import import_artifact_bundles
from omniship.plugins.discovery import load_plugins
from omniship.plugins.gitlab import GitLabCI
from omniship.plugins.gitlab.ci import GitLabCIGenerator
from omniship.workflow.model import Pipeline

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="GitLab jobs run on Linux runners"
)

CONFIG = """\
version: 1
check:
  compile:
    uses: core/command
    with:
      run: mkdir -p out && echo lib > out/libzova
      artifacts: [out/libzova]
  test:
    uses: core/noop
    needs: [compile]
build:
  package:
    uses: core/command
    with:
      run: mkdir -p dist && printf '#!/bin/sh\\necho ok\\n' > dist/tool && chmod +x dist/tool
      artifacts: [dist/tool]
  checksums:
    uses: core/command
    needs: [package]
    with:
      run: echo sum > SHA256SUMS
      artifacts: [SHA256SUMS]
ship:
  publish:
    uses: core/noop
"""
BUILD_REVISION = "a" * 40


def _gitlab_jobs(tmp_path: Path, *, forward: tuple[str, ...] = ()) -> dict:
    source = tmp_path / "source"
    source.mkdir()
    config_path = source / "omniship.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    config = load_config_from_file(config_path)
    # Set from workflow.py in real use; omniship.yaml does not carry it.
    for name in forward:
        config.check[name].forward_to_build = True
    (generated,) = GitLabCIGenerator(load_plugins()).generate(
        config,
        source / "workflow.py",
        config_path,
        Pipeline(targets=[GitLabCI()]),
    )
    return yaml.safe_load(generated.content)


def _run_job(
    name: str,
    jobs: dict,
    runs: Path,
    *,
    commit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Result:
    job = jobs[name]
    checkout = runs / name
    checkout.mkdir(parents=True)
    (checkout / "omniship.yaml").write_text(CONFIG, encoding="utf-8")
    for need in job.get("needs", []):
        producer = need if isinstance(need, str) else need["job"]
        if isinstance(need, dict) and not need["artifacts"]:
            continue
        for path in jobs[producer].get("artifacts", {}).get("paths", []):
            restored = checkout / path
            shutil.copytree(runs / producer / path, restored)
            for item in restored.rglob("*"):
                if item.is_file():
                    item.chmod(0o644)

    variables = {
        variable: value.replace("$CI_COMMIT_SHA", commit)
        for variable, value in job.get("variables", {}).items()
    }
    (script,) = job["script"]
    arguments = shlex.split(script)
    monkeypatch.chdir(checkout)
    return CliRunner().invoke(
        cli, arguments[arguments.index("run-node") :], env=variables
    )


@pytest.fixture(autouse=True)
def _clean_revision_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNISHIP_REVISION", raising=False)
    monkeypatch.delenv("OMNISHIP_EXPECTED_ARTIFACT_REVISION", raising=False)


def _build(jobs: dict, runs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("build-package", "build-checksums"):
        result = _run_job(
            name, jobs, runs, commit=BUILD_REVISION, monkeypatch=monkeypatch
        )
        assert result.exit_code == 0, result.output


def test_ship_job_imports_bundles_from_the_gitlab_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = _gitlab_jobs(tmp_path)
    runs = tmp_path / "runs"
    _build(jobs, runs, monkeypatch)

    result = _run_job(
        "ship-publish", jobs, runs, commit=BUILD_REVISION, monkeypatch=monkeypatch
    )

    assert result.exit_code == 0, result.output
    handoff = runs / "ship-publish" / ".omniship" / "handoff"
    assert sorted(path.name for path in handoff.iterdir()) == [
        "build-checksums",
        "build-package",
    ]
    # run-node restored the executable bit that the transfer dropped.
    (tool,) = (handoff / "build-package").rglob("tool")
    assert tool.stat().st_mode & 0o111
    artifacts = import_artifact_bundles(handoff, expected_revision=BUILD_REVISION)
    assert sorted(artifact.name for artifact in artifacts) == [
        "SHA256SUMS",
        "dist/tool",
    ]


def test_ship_job_rejects_bundles_from_another_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = _gitlab_jobs(tmp_path)
    runs = tmp_path / "runs"
    _build(jobs, runs, monkeypatch)

    result = _run_job(
        "ship-publish", jobs, runs, commit="b" * 40, monkeypatch=monkeypatch
    )

    assert result.exit_code != 0
    assert "Artifact Import Error" in result.output
    assert "expected revision" in result.output


def test_check_job_imports_its_check_dependency_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = _gitlab_jobs(tmp_path)
    runs = tmp_path / "runs"
    for name in ("check-compile", "check-test"):
        result = _run_job(
            name, jobs, runs, commit=BUILD_REVISION, monkeypatch=monkeypatch
        )
        assert result.exit_code == 0, result.output

    handoff = runs / "check-test" / ".omniship" / "check-handoff"
    artifacts = import_artifact_bundles(handoff, expected_revision=BUILD_REVISION)
    assert [artifact.name for artifact in artifacts] == ["out/libzova"]


def test_build_jobs_import_forwarded_check_bundles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = _gitlab_jobs(tmp_path, forward=("compile",))
    runs = tmp_path / "runs"

    assert {"job": "check-compile", "artifacts": True} in jobs["build-checksums"][
        "needs"
    ]
    # Ship still reads only Build bundles.
    assert "check-handoff" not in jobs["ship-publish"]["script"][0]
    for name in ("check-compile", "build-package", "build-checksums", "ship-publish"):
        result = _run_job(
            name, jobs, runs, commit=BUILD_REVISION, monkeypatch=monkeypatch
        )
        assert result.exit_code == 0, result.output

    # build-checksums read both its Build dependency and the Check bundle.
    script = jobs["build-checksums"]["script"][0]
    assert "--import-artifacts-root .omniship/handoff " in script
    assert "--import-artifacts-root .omniship/check-handoff " in script
    restored = runs / "build-checksums" / ".omniship" / "check-handoff"
    assert [artifact.name for artifact in import_artifact_bundles(restored)] == [
        "out/libzova"
    ]
    # Build exports only what it produced, so Ship never receives Check files.
    shipped = import_artifact_bundles(runs / "ship-publish" / ".omniship" / "handoff")
    assert "out/libzova" not in {artifact.name for artifact in shipped}
