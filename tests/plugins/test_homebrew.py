import hashlib
import runpy
from pathlib import Path

import pytest
import yaml

from omniship.core.context import ExecutionContext
from omniship.core.node import NodeInputs
from omniship.core.result import NodeStatus
from omniship.core.stage import Stage
from omniship.plugins.discovery import load_plugins
from omniship.plugins.github.actions import GitHubActionsGenerator
from omniship.plugins.homebrew import (
    Homebrew,
    HomebrewCask,
    HomebrewFormula,
    HomebrewMode,
    HomebrewTap,
)
from omniship.runtime import TaskContext, TaskFailure
from omniship.workflow.compiler import compile_pipeline


def context(tmp_path, env=None):
    archive = tmp_path / "demo.tar.gz"
    archive.write_bytes(b"release")
    ctx = TaskContext(tmp_path, env or {})
    ctx.artifacts.add(archive, name="release")
    return ctx


def test_tap_setup_validates_repository_name():
    assert HomebrewTap("acme/tools").github()["run"] == "brew tap acme/tools"
    with pytest.raises(ValueError):
        HomebrewTap("acme/tools; echo bad")


def test_example_uses_provider_secret_and_release_dependency():
    source = Path("examples/homebrew-release/workflow.py").resolve()
    pipeline = runpy.run_path(str(source))["pipeline"]
    config = compile_pipeline(pipeline, source)
    generated = GitHubActionsGenerator(load_plugins()).generate(
        config, source, source.parent / "omniship.yaml", pipeline
    )
    document = yaml.safe_load(
        next(item.content for item in generated if item.path.name == "ship.yml")
    )
    job = document["jobs"]["ship-update-tap"]
    assert any(
        step.get("env", {}).get("HOMEBREW_GITHUB_API_TOKEN")
        == "${{ secrets.HOMEBREW_TOKEN }}"
        for step in job["steps"]
    )
    assert "ship-github-release" in job["needs"]
    assert any(step.get("run") == "brew tap acme/tools" for step in job["steps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["formula", "cask"])
async def test_operations_delegate_to_facade(tmp_path, monkeypatch, kind):
    calls = []
    monkeypatch.setattr(Homebrew, kind, lambda self, **kwargs: calls.append(kwargs))
    operation = load_plugins().get_operation(f"homebrew/{kind}")
    result = await operation.execute(
        ExecutionContext(tmp_path, Stage.SHIP),
        NodeInputs(
            params={
                kind: "acme/tap/demo",
                "artifact": "release",
                "version": "1",
                "url": "https://example.com/demo.tgz",
            }
        ),
    )
    assert result.status == NodeStatus.SUCCESS
    assert calls[0][kind] == "acme/tap/demo"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/demo",
        "https://token@example.com/demo",
        "https://example.com/demo?token=secret",
    ],
)
def test_credentials_and_non_https_urls_are_rejected(tmp_path, url):
    with pytest.raises(ValueError):
        HomebrewFormula(
            formula="acme/tap/demo", artifact="release", version="1", url=url
        ).compile(Stage.SHIP, tmp_path)


def test_formula_pr_uses_verified_checksum_and_no_token_in_command(
    tmp_path, monkeypatch
):
    ctx = context(tmp_path, {"HOMEBREW_GITHUB_API_TOKEN": "secret"})
    calls = []
    monkeypatch.setattr(
        Homebrew, "_run", lambda self, args, **kwargs: calls.append(args) or ""
    )
    Homebrew(ctx).formula(
        formula="acme/tap/demo",
        artifact="release",
        version="1.2.3",
        url="https://example.com/demo.tar.gz",
    )
    assert calls[-1] == [
        "brew",
        "bump-formula-pr",
        "acme/tap/demo",
        "--version=1.2.3",
        "--url=https://example.com/demo.tar.gz",
        "--sha256=" + hashlib.sha256(b"release").hexdigest(),
        "--no-browse",
        "--no-fork",
    ]
    assert "secret" not in repr(calls)


def test_cask_write_only_does_not_require_token(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        Homebrew, "_run", lambda self, args, **kwargs: calls.append(args) or ""
    )
    Homebrew(context(tmp_path)).cask(
        cask="acme/tap/demo",
        artifact="release",
        version="1.2.3",
        url="https://example.com/demo.dmg",
        mode=HomebrewMode.WRITE_ONLY,
    )
    assert "bump-cask-pr" in calls[-1]
    assert "--write-only" in calls[-1]
    assert not any("push" in call for call in calls)


def test_missing_auth_fails_before_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Homebrew, "_run", lambda *_args, **_kwargs: pytest.fail("no external commands")
    )
    with pytest.raises(TaskFailure, match="HOMEBREW_GITHUB_API_TOKEN"):
        Homebrew(context(tmp_path)).formula(
            formula="acme/tap/demo",
            artifact="release",
            version="1",
            url="https://example.com/demo.tar.gz",
        )


def test_checksum_mismatch_prevents_update(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("0" * 64 + "  demo.tar.gz\n")
    ctx.artifacts.add(manifest)
    monkeypatch.setattr(
        Homebrew, "_run", lambda *_args, **_kwargs: pytest.fail("no external commands")
    )
    with pytest.raises(TaskFailure, match="checksum"):
        Homebrew(ctx).formula(
            formula="acme/tap/demo",
            artifact="release",
            checksum_artifact="SHA256SUMS",
            version="1",
            url="https://example.com/demo.tar.gz",
            mode=HomebrewMode.WRITE_ONLY,
        )


def test_direct_mode_requires_branch_and_clean_tap(tmp_path, monkeypatch):
    tap = tmp_path / "tap"
    tap.mkdir()
    calls = []

    def run(self, args, **kwargs):
        calls.append(args)
        if args[:2] == ["brew", "--repository"]:
            return str(tap)
        if "--show-toplevel" in args:
            return str(tap)
        if "status" in args:
            return " M Formula/unrelated.rb"
        return "main"

    monkeypatch.setattr(Homebrew, "_run", run)
    with pytest.raises(TaskFailure, match="clean"):
        Homebrew(context(tmp_path)).formula(
            formula="acme/tap/demo",
            artifact="release",
            version="1",
            url="https://example.com/demo.tar.gz",
            mode=HomebrewMode.DIRECT,
            branch="main",
        )
    assert not any("bump-formula-pr" in call for call in calls)


def test_typed_blocks_validate_and_compile(tmp_path):
    kwargs = dict(
        artifact="release", version="1", url="https://example.com/demo.tar.gz"
    )
    assert (
        HomebrewFormula(formula="acme/tap/demo", **kwargs)
        .compile(Stage.SHIP, tmp_path)[0]
        .uses
        == "homebrew/formula"
    )
    assert (
        HomebrewCask(cask="acme/tap/demo", **kwargs)
        .compile(Stage.SHIP, tmp_path)[0]
        .uses
        == "homebrew/cask"
    )
    with pytest.raises(ValueError):
        HomebrewFormula(formula="--evil", **kwargs).compile(Stage.SHIP, tmp_path)
    with pytest.raises(ValueError):
        HomebrewFormula(
            formula="acme/tap/demo", **kwargs, mode=HomebrewMode.DIRECT
        ).compile(Stage.SHIP, tmp_path)


def test_dry_run_never_invokes_external_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Homebrew,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("dry run must not execute"),
    )
    ctx = context(tmp_path)
    Homebrew(ctx).formula(
        formula="acme/tap/demo",
        artifact="release",
        version="1",
        url="https://example.com/demo.tar.gz",
        dry_run=True,
    )
    assert "Dry run" in ctx.log.lines[-1]


@pytest.mark.parametrize("ahead", [False, True])
def test_direct_push_is_explicit_and_rejects_unpublished_commits(
    tmp_path, monkeypatch, ahead
):
    tap = tmp_path / "tap"
    tap.mkdir()
    calls = []

    def run(self, args, **kwargs):
        calls.append(args)
        if args[:2] == ["brew", "--repository"] or "--show-toplevel" in args:
            return str(tap)
        if "status" in args:
            return ""
        if "--show-current" in args:
            return "main"
        if "rev-parse" in args:
            return "a" * 40
        if "ls-remote" in args:
            return ("b" if ahead else "a") * 40 + "\trefs/heads/main\n"
        return ""

    monkeypatch.setattr(Homebrew, "_run", run)
    kwargs = dict(
        formula="acme/tap/demo",
        artifact="release",
        version="1",
        url="https://example.com/demo.tar.gz",
        mode=HomebrewMode.DIRECT,
        branch="main",
    )
    if ahead:
        with pytest.raises(TaskFailure, match="remote"):
            Homebrew(context(tmp_path)).formula(**kwargs)
        assert not any("bump-formula-pr" in call for call in calls)
    else:
        Homebrew(context(tmp_path)).formula(**kwargs)
        assert "--commit" in calls[-2]
        assert calls[-1] == [
            "git",
            "-C",
            str(tap),
            "push",
            "origin",
            "HEAD:refs/heads/main",
        ]
