"""Real CLI assembly regression; enabled in the dedicated NAPI-RS CI job."""

import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from omniship.plugins.napi import Napi
from omniship.runtime import TaskContext


@pytest.mark.skipif(
    os.environ.get("OMNISHIP_TEST_NAPI_CLI") != "1",
    reason="requires @napi-rs/cli@3.0.0 and npm on PATH",
)
def test_real_napi_3_assembly_packs_platform_and_root(tmp_path):
    executable = shutil.which("napi")
    assert executable, "Install @napi-rs/cli@3.0.0 before enabling this test"
    cli_package = Path(executable).resolve().parents[1] / "package.json"
    assert json.loads(cli_package.read_text(encoding="utf-8"))["version"] == "3.0.0"
    workspace = tmp_path / "project with spaces"
    workspace.mkdir()
    package = {
        "name": "omniship-napi-fixture",
        "version": "1.2.3",
        "license": "MIT",
        "main": "index.js",
        "files": ["index.js", "*.node"],
        "napi": {"binaryName": "addon", "targets": ["x86_64-unknown-linux-gnu"]},
        # Any accidental lifecycle execution makes the test fail.
        "scripts": {"prepack": 'node -e "process.exit(99)"'},
    }
    source = workspace / "package.json"
    source.write_text(json.dumps(package), encoding="utf-8")
    original = source.read_bytes()
    native = workspace / "native"
    native.mkdir()
    binary = b"fixture bytes; assembly must not load or execute the native binary"
    (native / "addon.linux-x64-gnu.node").write_bytes(binary)
    (native / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    ctx = TaskContext(workspace, dict(os.environ))
    ctx.artifacts.add(native, name="native")

    artifacts = Napi(ctx).package(artifacts=("native",), output="dist/npm packages")

    assert [item.name for item in artifacts] == [
        "omniship-napi-fixture-linux-x64-gnu-1.2.3.tgz",
        "omniship-napi-fixture-1.2.3.tgz",
    ]
    with tarfile.open(artifacts[0].path) as archive:
        assert archive.extractfile("package/addon.linux-x64-gnu.node").read() == binary
        platform = json.load(archive.extractfile("package/package.json"))
        assert platform["version"] == "1.2.3"
    with tarfile.open(artifacts[1].path) as archive:
        root = json.load(archive.extractfile("package/package.json"))
        assert root["optionalDependencies"] == {platform["name"]: "1.2.3"}
        assert (
            archive.extractfile("package/index.js").read() == b"module.exports = {};\n"
        )
    assert source.read_bytes() == original
