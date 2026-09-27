import json
import runpy
from pathlib import Path

import pytest
import yaml

from omniship.core.stage import Stage
from omniship.plugins.discovery import load_plugins
from omniship.plugins.github.actions import GitHubActionsGenerator
from omniship.plugins.napi import Napi, NapiBuild, NapiPackage, NapiToolchain
from omniship.plugins.node import Node, NpmPublish
from omniship.runtime import TaskContext, TaskFailure
from omniship.workflow.compiler import compile_pipeline


def test_npm_artifact_selection_is_ordered_and_fails_closed(tmp_path, monkeypatch):
    ctx = TaskContext(tmp_path, {})
    calls = []
    monkeypatch.setattr(Node, "_run", lambda self, args: calls.append(args))
    with pytest.raises(TaskFailure, match="No npm"):
        Node(ctx).publish(from_artifacts=True, dry_run=True)
    for name, order in (("root.tgz", "1"), ("platform.tgz", "0")):
        path = tmp_path / name
        path.write_bytes(b"tgz")
        ctx.artifacts.add(path, metadata={"npm.package": "true", "npm.order": order})
    Node(ctx).publish(from_artifacts=True, dry_run=True)
    assert [Path(call[2]).name for call in calls] == ["platform.tgz", "root.tgz"]


def test_napi_blocks_and_requirement(tmp_path):
    spec = NapiBuild(target="x86_64-unknown-linux-gnu").compile(Stage.BUILD, tmp_path)[
        0
    ]
    assert spec.uses == "napi/build"
    assert (
        NapiPackage(artifacts=("native",)).compile(Stage.BUILD, tmp_path)[0].uses
        == "napi/package"
    )
    with pytest.raises(ValueError):
        NapiToolchain(version="latest")
    assert "@napi-rs/cli@3.0.0" in NapiToolchain(version="3.0.0").github()["run"]


def test_example_generates_native_jobs_and_package_handoff(tmp_path):
    source = Path("examples/napi-release/workflow.py").resolve()
    pipeline = runpy.run_path(str(source))["pipeline"]
    config = compile_pipeline(pipeline, source)
    registry = load_plugins()
    generated = GitHubActionsGenerator(registry).generate(
        config, source, source.parent / "omniship.yaml", pipeline
    )
    build = yaml.safe_load(
        next(item.content for item in generated if item.path.name == "build.yml")
    )
    steps = build["jobs"]["build-napi-package"]["steps"]
    assert any("@napi-rs/cli@3.0.0" in step.get("run", "") for step in steps)
    assert len(build["jobs"]["build-napi-package"]["needs"]) >= 2


def test_assembly_rejects_missing_platform_binary(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"name":"addon","version":"1.0.0"}')
    binary = tmp_path / "addon.node"
    binary.write_bytes(b"native")
    ctx = TaskContext(tmp_path, {})
    ctx.artifacts.add(binary)

    def run(self, args):
        if args[1] == "create-npm-dirs":
            directory = Path(args[-1]) / "missing-platform"
            directory.mkdir()
            (directory / "package.json").write_text("{}")
        assert args[0] != "npm"
        return ""

    monkeypatch.setattr(Napi, "_run", run)
    with pytest.raises(TaskFailure, match="Missing native binary"):
        Napi(ctx).package(artifacts=("addon.node",))


def test_build_registers_only_fresh_output(tmp_path, monkeypatch):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/stale.node").write_bytes(b"stale")
    calls = []

    def run(self, args):
        calls.append(args)
        output = Path(args[args.index("--output-dir") + 1])
        (output / "addon.linux-x64-gnu.node").write_bytes(b"binary")
        (output / "index.js").write_text("loader")
        return ""

    monkeypatch.setattr(Napi, "_run", run)
    ctx = TaskContext(tmp_path, {})
    artifact = Napi(ctx).build(target="x86_64-unknown-linux-gnu", output="dist")
    assert "--platform" in calls[0]
    assert artifact.name == "napi-x86_64-unknown-linux-gnu"
    assert not (artifact.path / "stale.node").exists()
    assert (artifact.path / "index.js").exists()


def test_build_rejects_no_output_and_escaping_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Napi, "_run", lambda *_: "")
    with pytest.raises(TaskFailure, match="no native"):
        Napi(TaskContext(tmp_path, {})).build(target="x86_64-unknown-linux-gnu")
    with pytest.raises(ValueError, match="workspace"):
        Napi(TaskContext(tmp_path, {})).build(
            target="x86_64-unknown-linux-gnu", output="../bad"
        )


def test_assembly_validates_and_packs_platform_before_root(tmp_path, monkeypatch):
    root = {"name": "addon", "version": "1.2.3", "napi": {"binaryName": "addon"}}
    original = json.dumps(root)
    (tmp_path / "package.json").write_text(original)
    native = tmp_path / "native"
    native.mkdir()
    (native / "addon.linux-x64-gnu.node").write_bytes(b"binary")
    (native / "index.js").write_text("loader")
    ctx = TaskContext(tmp_path, {})
    ctx.artifacts.add(native, name="native")
    calls = []

    def run(self, args):
        calls.append(args)
        if args[:2] == ["napi", "create-npm-dirs"]:
            package = Path(args[args.index("--npm-dir") + 1]) / "linux-x64-gnu"
            package.mkdir()
            (package / "package.json").write_text(
                json.dumps({"name": "addon-linux-x64-gnu", "version": "1.2.3"})
            )
        if args[:2] == ["napi", "artifacts"]:
            package = Path(args[args.index("--npm-dir") + 1]) / "linux-x64-gnu"
            (package / "addon.linux-x64-gnu.node").write_bytes(b"binary")
        if args[:2] == ["npm", "pack"]:
            assert "--ignore-scripts" in args
            package = json.loads((Path(args[2]) / "package.json").read_text())
            if package["name"] == "addon":
                assert package["optionalDependencies"] == {
                    "addon-linux-x64-gnu": "1.2.3"
                }
            filename = package["name"] + "-1.2.3.tgz"
            (Path(args[args.index("--pack-destination") + 1]) / filename).write_bytes(
                b"tarball"
            )
            return json.dumps([{"filename": filename}])
        return ""

    monkeypatch.setattr(Napi, "_run", run)
    result = Napi(ctx).package(artifacts=("native",))
    assert [item.name for item in result] == [
        "addon-linux-x64-gnu-1.2.3.tgz",
        "addon-1.2.3.tgz",
    ]
    assert (tmp_path / "package.json").read_text() == original
    assert all("publish" not in call for call in calls)


def test_assembly_missing_artifact_fails_before_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(Napi, "_run", lambda *_: pytest.fail("must not execute"))
    with pytest.raises(TaskFailure, match="Missing"):
        Napi(TaskContext(tmp_path, {})).package(artifacts=("missing",))


def test_assembly_rejects_ambiguous_artifacts(tmp_path, monkeypatch):
    ctx = TaskContext(tmp_path, {})
    for name in ("a.node", "b.node"):
        path = tmp_path / name
        path.write_bytes(b"native")
        ctx.artifacts.add(path, name="native")
    monkeypatch.setattr(Napi, "_run", lambda *_: pytest.fail("must not execute"))
    with pytest.raises(TaskFailure, match="ambiguous"):
        Napi(ctx).package(artifacts=("native",))


def test_npm_publishes_prebuilt_tarballs_without_scripts(tmp_path, monkeypatch):
    wheel = tmp_path / "addon.tgz"
    wheel.write_bytes(b"tarball")
    ctx = TaskContext(tmp_path, {})
    ctx.artifacts.add(wheel, name="npm-package")
    calls = []
    monkeypatch.setattr(Node, "_run", lambda self, args: calls.append(args))
    Node(ctx).publish(files=("npm-package",), dry_run=True)
    assert calls == [["npm", "publish", str(wheel), "--ignore-scripts", "--dry-run"]]
    assert NpmPublish(files=("npm-package",)).compile(Stage.SHIP, tmp_path)[0].params[
        "files"
    ] == ["npm-package"]
