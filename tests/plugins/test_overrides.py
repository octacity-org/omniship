import pytest

from omniship.plugins.overrides import (
    add_extra_jobs,
    apply_overrides,
    normalize_extra_jobs,
    normalize_overrides,
)


def _document() -> dict:
    return {
        "jobs": {
            "release": {
                "runs-on": "ubuntu-24.04",
                "steps": [
                    {"uses": "actions/checkout@abc"},
                    {"name": "Run Release", "run": "omniship run-node"},
                ],
            }
        }
    }


def _apply(document: dict, overrides: dict) -> dict:
    return apply_overrides(document, normalize_overrides(overrides), "ship.yml")


def test_override_sets_new_and_existing_keys() -> None:
    document = _apply(
        _document(),
        {
            "jobs.release.if": "${{ inputs.publish }}",
            "jobs.release.runs-on": "macos-15",
        },
    )

    assert document["jobs"]["release"]["if"] == "${{ inputs.publish }}"
    assert document["jobs"]["release"]["runs-on"] == "macos-15"


def test_override_addresses_steps_by_name_or_index() -> None:
    document = _apply(
        _document(),
        {
            "jobs.release.steps.Run Release.continue-on-error": True,
            "jobs.release.steps.0.with": {"fetch-depth": 0},
        },
    )

    steps = document["jobs"]["release"]["steps"]
    assert steps[0]["with"] == {"fetch-depth": 0}
    assert steps[1]["continue-on-error"] is True


def test_override_path_can_be_a_tuple_for_names_with_dots() -> None:
    document = _document()
    document["jobs"]["release"]["steps"][1]["name"] = "Set up Python 3.14"

    document = _apply(
        document, {("jobs", "release", "steps", "Set up Python 3.14", "if"): "x"}
    )

    assert document["jobs"]["release"]["steps"][1]["if"] == "x"


def test_overrides_leave_the_original_document_unchanged() -> None:
    original = _document()
    shared = original["jobs"]["release"]

    _apply(original, {"jobs.release.if": "x"})

    assert "if" not in shared


def test_override_appends_and_removes_list_items() -> None:
    document = _apply(
        _document(),
        {
            "jobs.release.steps.-": {"run": "echo done"},
            "jobs.release.steps.0": None,
        },
    )

    assert document["jobs"]["release"]["steps"] == [
        {"name": "Run Release", "run": "omniship run-node"},
        {"run": "echo done"},
    ]


def test_override_none_removes_a_key() -> None:
    document = _apply(_document(), {"jobs.release.runs-on": None})

    assert "runs-on" not in document["jobs"]["release"]


def test_override_values_are_copied() -> None:
    shared = {"fetch-depth": 0}
    document = _apply(
        _document(),
        {"jobs.release.steps.0.with": shared, "jobs.release.steps.1.with": shared},
    )

    steps = document["jobs"]["release"]["steps"]
    # Distinct objects, so YAML output never uses anchors.
    assert steps[0]["with"] is not steps[1]["with"]
    assert steps[0]["with"] is not shared


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("jobs.relaese.if", "'relaese' does not exist"),
        ("jobs.release.steps.Missing.if", "no item named 'Missing'"),
        ("jobs.release.steps.5.if", "index 5 is out of range"),
        ("jobs.release.runs-on.os", "'runs-on' is not a mapping or list"),
        ("jobs.release.timeout", "'timeout' does not exist"),
    ],
)
def test_override_rejects_paths_that_do_not_exist(path: str, message: str) -> None:
    overrides = {path: None if path.endswith("timeout") else "value"}

    with pytest.raises(ValueError, match=f"ship.yml override '{path}':.*{message}"):
        _apply(_document(), overrides)


def test_override_rejects_ambiguous_step_names() -> None:
    document = _document()
    document["jobs"]["release"]["steps"][0]["name"] = "Run Release"

    with pytest.raises(ValueError, match="2 items are named 'Run Release'"):
        _apply(document, {"jobs.release.steps.Run Release.if": "x"})


@pytest.mark.parametrize(
    "overrides",
    [{"": 1}, {"jobs..if": 1}, {(): 1}, {("jobs", 1.5): 1}, {3: 1}, ["jobs.x"]],
)
def test_override_rejects_malformed_paths(overrides: object) -> None:
    with pytest.raises((TypeError, ValueError), match="override"):
        normalize_overrides(overrides)


def test_extra_jobs_are_added_and_cannot_replace_generated_ones() -> None:
    jobs = {"release": {}}

    add_extra_jobs(jobs, normalize_extra_jobs({"notify": {"runs-on": "x"}}), "ship.yml")

    assert jobs == {"release": {}, "notify": {"runs-on": "x"}}
    with pytest.raises(ValueError, match="ship.yml extra job 'release' .*generated"):
        add_extra_jobs(jobs, normalize_extra_jobs({"release": {}}), "ship.yml")


@pytest.mark.parametrize("extra_jobs", [{"": {}}, {"x": "run"}, {1: {}}, ["x"]])
def test_extra_jobs_must_map_names_to_mappings(extra_jobs: object) -> None:
    with pytest.raises(TypeError, match="extra_jobs"):
        normalize_extra_jobs(extra_jobs)
