import hashlib
import io
import json
import urllib.error
from pathlib import Path

import pytest

from omniship.core.artifact import Artifact
from omniship.plugins.github import GitHub
from omniship.plugins.github import runtime as github_runtime
from omniship.plugins.github.runtime import is_prerelease_tag
from omniship.runtime import TaskContext, TaskFailure

API = "https://api.github.com/repos/octacity-org/demo"
UPLOADS = "https://uploads.github.com/repos/octacity-org/demo/releases/7/assets"


RELEASE = {
    "id": 7,
    "html_url": "https://github.com/octacity-org/demo/releases/v1.0.0",
    "upload_url": f"{UPLOADS}{{?name,label}}",
    "assets": [],
}


class FakeGitHub:
    """GitHub's release API: by-tag lookups return published releases only."""

    def __init__(self) -> None:
        self.published: dict[str, dict] = {}
        self.releases: list[dict] = []
        self.requests: list[tuple[str, str, bytes | None]] = []

    def urlopen(self, request, timeout=None):
        method = request.get_method()
        url = request.full_url
        self.requests.append((method, url, request.data))
        tag_prefix = f"{API}/releases/tags/"
        if method == "GET" and url.startswith(tag_prefix):
            tag = url.removeprefix(tag_prefix)
            if tag not in self.published:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return self._response(self.published[tag])
        if method == "GET" and url.startswith(f"{API}/releases?per_page=100&page="):
            page = int(url.rsplit("=", 1)[1])
            return self._response(self.releases[(page - 1) * 100 : page * 100])
        if method == "POST" and url == f"{API}/releases":
            return self._response({**RELEASE, "draft": True})
        if method == "PATCH" and url == f"{API}/releases/7":
            return self._response({**RELEASE, **json.loads(request.data)})
        if method == "POST" and url.startswith(UPLOADS):
            return self._response({})
        raise AssertionError(f"unexpected request {method} {url}")

    def calls(self, method: str, url: str) -> list[dict]:
        return [
            json.loads(data)
            for request_method, request_url, data in self.requests
            if request_method == method and request_url == url
        ]

    @staticmethod
    def _response(document: object):
        return io.BytesIO(json.dumps(document).encode("utf-8"))


@pytest.fixture
def api(monkeypatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(github_runtime.urllib.request, "urlopen", fake.urlopen)
    return fake


def _context(tmp_path: Path, *artifacts: Artifact, **env: str) -> TaskContext:
    return TaskContext(
        tmp_path,
        {"GITHUB_TOKEN": "token", "GITHUB_SHA": "abc123", **env},
        artifacts=artifacts,
    )


def _wheel(tmp_path: Path, name: str = "demo 1.0+local.whl") -> Artifact:
    path = tmp_path / name
    path.write_bytes(b"wheel")
    return Artifact.from_path(path)


def test_release_targets_built_commit_and_encodes_asset_names(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    GitHub(_context(tmp_path, _wheel(tmp_path))).release("octacity-org/demo", "v1.0.0")

    (created,) = api.calls("POST", f"{API}/releases")
    assert created["target_commitish"] == "abc123"
    uploads = [url for method, url, _ in api.requests if url.startswith(UPLOADS)]
    assert uploads == [f"{UPLOADS}?name=demo%201.0%2Blocal.whl"]


def test_release_is_published_only_after_every_asset_is_uploaded(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    result = GitHub(_context(tmp_path, _wheel(tmp_path, "app.whl"))).release(
        "octacity-org/demo", "v1.0.0"
    )

    writes = [
        (method, url.split("?")[0])
        for method, url, _ in api.requests
        if method != "GET"
    ]
    assert writes == [
        ("POST", f"{API}/releases"),
        ("POST", UPLOADS),
        ("PATCH", f"{API}/releases/7"),
    ]
    (created,) = api.calls("POST", f"{API}/releases")
    assert created["draft"] is True
    assert api.calls("PATCH", f"{API}/releases/7") == [{"draft": False}]
    assert result.release_url.endswith("/releases/v1.0.0")


def test_draft_release_stays_a_draft(tmp_path: Path, api: FakeGitHub) -> None:
    context = _context(tmp_path, _wheel(tmp_path, "app.whl"))

    GitHub(context).release("octacity-org/demo", "v1.0.0", draft=True)

    assert not any(method == "PATCH" for method, _, _ in api.requests)
    assert any("Prepared draft release v1.0.0" in line for line in context.log.lines)


def test_release_refuses_to_change_a_published_release(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    api.published["v1.0.0"] = {**RELEASE, "draft": False}

    with pytest.raises(TaskFailure, match="v1.0.0 is already published"):
        GitHub(_context(tmp_path, _wheel(tmp_path, "app.whl"))).release(
            "octacity-org/demo", "v1.0.0"
        )
    assert all(method == "GET" for method, _, _ in api.requests)


def test_release_rerun_reuses_draft_and_skips_identical_assets(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    uploaded = _wheel(tmp_path, "uploaded.whl")
    missing = _wheel(tmp_path, "missing.whl")
    other = {**RELEASE, "id": 3, "tag_name": "v0.9.0", "draft": True}
    api.releases = [
        *[{**other, "id": 100 + index} for index in range(100)],
        {
            **RELEASE,
            "tag_name": "v1.0.0",
            "draft": True,
            "assets": [
                {
                    "name": "uploaded.whl",
                    "digest": "sha256:" + hashlib.sha256(b"wheel").hexdigest(),
                }
            ],
        },
    ]

    result = GitHub(_context(tmp_path, uploaded, missing)).release(
        "octacity-org/demo", "v1.0.0"
    )

    # The draft is on the second page of the release list.
    assert f"{API}/releases?per_page=100&page=2" in [url for _, url, _ in api.requests]
    assert api.calls("POST", f"{API}/releases") == []
    uploads = [url for method, url, _ in api.requests if url.startswith(UPLOADS)]
    assert uploads == [f"{UPLOADS}?name=missing.whl"]
    assert api.calls("PATCH", f"{API}/releases/7") == [{"draft": False}]
    assert result.release_url.endswith("/releases/v1.0.0")


def test_release_rerun_rejects_conflicting_existing_asset(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    api.releases = [
        {
            **RELEASE,
            "tag_name": "v1.0.0",
            "draft": True,
            "assets": [{"name": "app.whl", "digest": "sha256:" + "0" * 64}],
        }
    ]

    with pytest.raises(TaskFailure, match="app.whl already exists"):
        GitHub(_context(tmp_path, _wheel(tmp_path, "app.whl"))).release(
            "octacity-org/demo", "v1.0.0"
        )
    assert not any(method == "PATCH" for method, _, _ in api.requests)


@pytest.mark.parametrize(
    ("tag", "prerelease"),
    [
        ("v1.2.0", False),
        ("v1.2.0-rc.1", True),
        ("1.2.0-alpha", True),
        ("v1.2.0+build-5", False),
        ("v1.2.0-beta+build.5", True),
    ],
)
def test_prerelease_tags_follow_semver(tag: str, prerelease: bool) -> None:
    assert is_prerelease_tag(tag) is prerelease


@pytest.mark.parametrize(
    ("tag", "prerelease"), [("v1.0.0", False), ("v1.1.0-rc.1", True)]
)
def test_auto_prerelease_is_derived_from_the_tag(
    tmp_path: Path,
    api: FakeGitHub,
    tag: str,
    prerelease: bool,
) -> None:
    GitHub(_context(tmp_path)).release("octacity-org/demo", tag, prerelease="auto")

    (created,) = api.calls("POST", f"{API}/releases")
    assert created["prerelease"] is prerelease


def test_release_rejects_tag_different_from_pushed_tag(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    context = _context(tmp_path, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v1.0.1")

    with pytest.raises(TaskFailure, match="does not match the pushed tag 'v1.0.1'"):
        GitHub(context).release("octacity-org/demo", "v1.0.0")
    assert api.requests == []


def test_release_rejects_explicit_directory_assets(tmp_path: Path, api) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("site", encoding="utf-8")

    with pytest.raises(TaskFailure, match="must be a file"):
        GitHub(_context(tmp_path, Artifact.from_path(site))).release(
            "octacity-org/demo", "v1.0.0", files=["site"]
        )


def test_release_reports_skipped_directory_artifacts(tmp_path: Path, api) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("site", encoding="utf-8")
    context = _context(tmp_path, Artifact.from_path(site), _wheel(tmp_path, "a.whl"))

    result = GitHub(context).release("octacity-org/demo", "v1.0.0")

    assert result.uploaded_count == 1
    assert any(
        "Skipping directory artifact 'site'" in line for line in context.log.lines
    )


def test_release_file_references_match_whole_path_segments(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    context = _context(
        tmp_path, _wheel(tmp_path, "myapp.whl"), _wheel(tmp_path, "app.whl")
    )

    GitHub(context).release("octacity-org/demo", "v1.0.0", files=["app.whl"])

    uploads = [url for method, url, _ in api.requests if url.startswith(UPLOADS)]
    assert uploads == [f"{UPLOADS}?name=app.whl"]


def test_release_file_reference_prefers_an_exact_name(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    (tmp_path / "linux").mkdir()
    shadow = tmp_path / "linux" / "tool"
    shadow.write_bytes(b"linux")
    wanted = tmp_path / "tool-any"
    wanted.write_bytes(b"any")
    context = _context(
        tmp_path,
        Artifact.from_path(shadow, name="tool-linux"),
        Artifact.from_path(wanted, name="tool"),
    )

    GitHub(context).release("octacity-org/demo", "v1.0.0", files=["tool"])

    uploads = [url for method, url, _ in api.requests if url.startswith(UPLOADS)]
    assert uploads == [f"{UPLOADS}?name=tool"]


def test_release_rejects_duplicate_asset_names_before_creating_a_draft(
    tmp_path: Path,
    api: FakeGitHub,
) -> None:
    variants = []
    for runner in ("linux", "macos"):
        (tmp_path / runner).mkdir()
        path = tmp_path / runner / "tool"
        path.write_bytes(runner.encode())
        variants.append(Artifact.from_path(path))

    with pytest.raises(TaskFailure, match="unique names.*named tool"):
        GitHub(_context(tmp_path, *variants)).release("octacity-org/demo", "v1.0.0")
    with pytest.raises(TaskFailure, match="matches 2 artifacts"):
        GitHub(_context(tmp_path, *variants)).release(
            "octacity-org/demo", "v1.0.0", files=["tool"]
        )
    assert api.requests == []
