import asyncio
import hashlib
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from omniship.core.artifact import Artifact
from omniship.core.context import ExecutionContext
from omniship.core.executor import StageExecutor
from omniship.core.node import Node
from omniship.core.result import NodeStatus
from omniship.core.stage import Stage
from omniship.plugins.packaging import (
    Archive,
    Checksums,
    TarGz,
    Zip,
    register_packaging_plugin,
)
from omniship.plugins.registry import PluginRegistry
from omniship.runtime import TaskContext, TaskFailure


def test_archive_creates_reproducible_tar_gz_and_zip_artifacts(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "build" / "demo"
    binary.parent.mkdir()
    binary.write_bytes(b"binary")
    binary.chmod(0o755)
    executable = bool(binary.stat().st_mode & stat.S_IXUSR)
    expected_mode = 0o755 if executable else 0o644
    context = TaskContext(tmp_path, {})
    archive = Archive(context)

    tar_artifact = archive.tar_gz(
        output="dist/demo.tar.gz",
        files={"build/demo": "demo"},
        reproducible=True,
    )
    first = tar_artifact.path.read_bytes()
    archive.tar_gz(
        output="dist/demo.tar.gz",
        files={"build/demo": "demo"},
        reproducible=True,
    )
    zip_artifact = archive.zip(
        output="dist/demo.zip",
        files={"build/demo": "demo"},
        reproducible=True,
    )

    assert tar_artifact.path.read_bytes() == first
    assert tar_artifact in context.artifacts.values
    assert zip_artifact in context.artifacts.values
    with tarfile.open(tar_artifact.path, "r:gz") as bundle:
        member = bundle.getmember("demo")
        assert member.mtime == 0
        assert member.uid == member.gid == 0
        assert member.mode == expected_mode
    with zipfile.ZipFile(zip_artifact.path) as bundle:
        info = bundle.getinfo("demo")
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert info.external_attr >> 16 == stat.S_IFREG | expected_mode


def test_archive_rejects_sources_outside_the_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-package-input"
    outside.write_text("unsafe", encoding="utf-8")

    with pytest.raises(TaskFailure, match="escapes workspace"):
        Archive(TaskContext(tmp_path, {})).zip(
            output="dist/demo.zip",
            files={str(outside): "outside"},
        )


def test_checksums_creates_a_sorted_sha256_manifest(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    context = TaskContext(
        tmp_path,
        {},
        artifacts=(
            Artifact.from_path(second, name="b.txt"),
            Artifact.from_path(first, name="a.txt"),
        ),
    )

    manifest = Checksums(context).sha256(output="dist/SHA256SUMS.txt")

    assert manifest.path.read_text(encoding="utf-8") == (
        f"{hashlib.sha256(b'a').hexdigest()}  a.txt\n"
        f"{hashlib.sha256(b'b').hexdigest()}  b.txt\n"
    )
    assert manifest in context.artifacts.values


@pytest.mark.parametrize(
    ("block_type", "suffix", "operation"),
    [(TarGz, ".tar.gz", "packaging/tar-gz"), (Zip, ".zip", "packaging/zip")],
)
def test_archive_blocks_name_files_from_the_pushed_tag(
    tmp_path: Path, block_type: type, suffix: str, operation: str
) -> None:
    binary = tmp_path / "build" / "demo"
    binary.parent.mkdir()
    binary.write_bytes(b"binary")
    block = block_type(
        output=f"dist/demo-{{version}}{suffix}", files={"build/demo": "demo-{tag}/demo"}
    )
    (spec,) = block.compile(Stage.BUILD, tmp_path)
    registry = PluginRegistry()
    register_packaging_plugin(registry)
    context = ExecutionContext(
        tmp_path,
        Stage.BUILD,
        env={"OMNISHIP_REF_TYPE": "tag", "OMNISHIP_REF_NAME": "v3.1.0"},
    )

    result = asyncio.run(
        StageExecutor(registry).execute_node(
            Node(
                id=spec.name,
                stage=Stage.BUILD,
                operation_name=operation,
                inputs=spec.params,
            ),
            context,
        )
    )

    assert result.status == NodeStatus.SUCCESS, result.error_message
    # Braces cannot appear in node names, so the name keeps the words.
    assert spec.name == f"demo-version-{suffix.lstrip('.').replace('.', '-')}"
    (artifact,) = result.artifacts
    assert artifact.path == tmp_path / f"dist/demo-3.1.0{suffix}"
    if suffix == ".zip":
        assert zipfile.ZipFile(artifact.path).namelist() == ["demo-v3.1.0/demo"]
    else:
        assert tarfile.open(artifact.path).getnames() == ["demo-v3.1.0/demo"]


def test_archive_blocks_without_placeholders_compile_unchanged(tmp_path: Path) -> None:
    (spec,) = TarGz(output="dist/demo.tar.gz", files={"a": "b"}).compile(
        Stage.BUILD, tmp_path
    )

    assert spec.params == {
        "output": "dist/demo.tar.gz",
        "files": {"a": "b"},
        "reproducible": True,
    }
