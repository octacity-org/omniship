"""GitLab CI target: placements and the `.gitlab-ci.yml` generator."""

import re
import shlex
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import ClassVar

import yaml

from omniship.config.models import NodeConfig, OmniShipConfig
from omniship.core.execution import (
    REF_NAME_ENV,
    REF_TYPE_ENV,
    REVISION_ENV,
    SecretRef,
    placement_for,
)
from omniship.core.stage import Stage
from omniship.plugins.api import GeneratedFile
from omniship.plugins.registry import PluginRegistry
from omniship.workflow.model import GitPluginPackage, Pipeline, PluginPackage

CONFIG_FILENAME = ".gitlab-ci.yml"
LOCK_FILENAME = "omniship.lock"
# uv's own GitLab guide uses the Debian "trixie-slim" image with Python; OmniShip
# itself requires Python 3.14. Override with GitLabCI(image=...).
DEFAULT_IMAGE = "ghcr.io/astral-sh/uv:0.13.8-python3.14-trixie-slim"
# Build jobs export bundles to <ARTIFACT_PATH>/<job>; GitLab restores job
# artifacts at the same path, so importers read the whole directory.
ARTIFACT_PATH = ".omniship/handoff"
EXPECTED_ARTIFACT_REVISION_ENV = "OMNISHIP_EXPECTED_ARTIFACT_REVISION"
# Matches the GitHub Actions target's ``v*`` tag trigger for Ship.
SHIP_RULE = "$CI_COMMIT_TAG =~ /^v/"

_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESERVED_VARIABLES = frozenset(
    {REF_NAME_ENV, REF_TYPE_ENV, REVISION_ENV, EXPECTED_ARTIFACT_REVISION_ENV}
)


@dataclass(frozen=True)
class GitLabJob:
    """Placement of one task on GitLab CI (Linux runners).

    A custom ``image`` must provide a shell and ``uvx``; jobs start OmniShip
    with ``uvx --from omniship==<locked version>``.
    """

    target: ClassVar[str] = "gitlab/ci"
    image: str | None = None
    tags: tuple[str, ...] = ()
    variables: Mapping[str, str | SecretRef] = field(default_factory=dict)
    timeout_minutes: int | None = None

    def __post_init__(self) -> None:
        if self.image is not None and (
            not isinstance(self.image, str) or not self.image.strip()
        ):
            raise TypeError("GitLab job image must be a non-empty string")
        if isinstance(self.tags, (str, bytes)):
            raise TypeError("GitLab job tags must be a sequence of strings")
        tags = tuple(self.tags)
        if any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise TypeError("GitLab job tags must be non-empty strings")
        if len(set(tags)) != len(tags):
            raise ValueError("GitLab job tags must be unique")
        object.__setattr__(self, "tags", tags)
        variables = dict(self.variables)
        for name, value in variables.items():
            if not isinstance(name, str) or _VARIABLE_NAME.fullmatch(name) is None:
                raise ValueError(f"GitLab variable name is invalid: {name!r}")
            if name in _RESERVED_VARIABLES:
                raise ValueError(f"GitLab variable {name} is set by OmniShip")
            if not isinstance(value, (str, SecretRef)):
                raise TypeError(
                    "GitLab variables must map names to strings or secret references"
                )
        object.__setattr__(self, "variables", variables)
        if self.timeout_minutes is not None and (
            not isinstance(self.timeout_minutes, int)
            or isinstance(self.timeout_minutes, bool)
            or self.timeout_minutes < 1
        ):
            raise ValueError("GitLab job timeout must be a positive number of minutes")


@dataclass(frozen=True)
class GitLabCI:
    """Generate `.gitlab-ci.yml`; Ship jobs run only for ``v*`` tags."""

    name: ClassVar[str] = "gitlab/ci"
    image: str = DEFAULT_IMAGE

    def __post_init__(self) -> None:
        if not isinstance(self.image, str) or not self.image.strip():
            raise TypeError("GitLab CI image must be a non-empty string")

    @property
    def default_job(self) -> GitLabJob:
        return GitLabJob()

    @staticmethod
    def secret(name: str) -> SecretRef:
        """Reference a GitLab CI/CD variable by name."""

        return SecretRef(name)

    def job(
        self,
        *,
        image: str | None = None,
        tags: Iterable[str] = (),
        variables: Mapping[str, str | SecretRef] | None = None,
        timeout_minutes: int | None = None,
    ) -> GitLabJob:
        return GitLabJob(
            image=image,
            tags=tuple(tags) if not isinstance(tags, (str, bytes)) else tags,
            variables={} if variables is None else variables,
            timeout_minutes=timeout_minutes,
        )


def _locked_document(workspace_root: Path) -> dict[str, object]:
    lock_path = workspace_root / LOCK_FILENAME
    if not lock_path.is_file():
        return {}
    try:
        return tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid OmniShip lock file {lock_path}: {exc}") from exc


def _omniship_version(lock: Mapping[str, object]) -> str:
    omniship = lock.get("omniship", {})
    locked = omniship.get("version") if isinstance(omniship, Mapping) else None
    if isinstance(locked, str) and locked:
        return locked
    return distribution_version("omniship")


def _plugin_requirement(
    plugin: PluginPackage | GitPluginPackage,
    lock: Mapping[str, object],
) -> str:
    if isinstance(plugin, PluginPackage):
        if plugin.index is not None:
            raise ValueError(
                f"Plugin '{plugin.name}' uses a private index, which the "
                "gitlab/ci target does not support yet"
            )
        return plugin.requirement
    if plugin.commit is None:
        sources = lock.get("plugin_sources", [])
        for entry in sources if isinstance(sources, list) else []:
            if (
                isinstance(entry, Mapping)
                and entry.get("type") == "git"
                and entry.get("name") == plugin.name
                and entry.get("repository") == plugin.repository
                and entry.get("version") == plugin.version
                and isinstance(entry.get("commit"), str)
            ):
                return GitPluginPackage(
                    plugin.name,
                    plugin.repository,
                    entry["commit"],
                    version=plugin.version,
                ).requirement
        raise ValueError(
            f"Git plugin '{plugin.name}' has no locked commit; pass commit= or "
            "lock it with a GitHub Actions target"
        )
    return plugin.requirement


def _variable_document(value: str | SecretRef) -> object:
    if isinstance(value, SecretRef):
        return f"${value.name}"
    if "$" in value:
        return {"value": value, "expand": False}
    return value


class GitLabCIGenerator:
    name = "gitlab/ci"

    def __init__(self, registry: PluginRegistry) -> None:
        self.registry = registry

    def generate(
        self,
        config: OmniShipConfig,
        source_path: Path,
        config_path: Path,
        pipeline: Pipeline,
    ) -> tuple[GeneratedFile, ...]:
        targets = [
            target for target in pipeline.targets if isinstance(target, GitLabCI)
        ]
        if not targets:
            return ()
        if len(targets) > 1:
            raise ValueError("Pipeline has more than one GitLab CI target")
        gitlab = targets[0]

        workspace_root = source_path.parent.resolve()
        source = source_path.resolve().relative_to(workspace_root)
        pipeline_config = config_path.resolve().relative_to(workspace_root)
        lock = _locked_document(workspace_root)
        omniship_command = [
            "uvx",
            "--from",
            f"omniship=={_omniship_version(lock)}",
        ]
        for plugin in pipeline.plugins:
            omniship_command.extend(["--with", _plugin_requirement(plugin, lock)])
        omniship_command.append("omniship")

        jobs: dict[str, dict[str, object]] = {}
        root_needs: list[str] = []
        build_jobs: list[str] = []
        for stage, nodes in (
            (Stage.CHECK, config.check),
            (Stage.BUILD, config.build),
            (Stage.SHIP, config.ship),
        ):
            stage_jobs = self._stage_jobs(
                stage,
                nodes,
                jobs,
                gitlab=gitlab,
                root_needs=root_needs,
                build_jobs=build_jobs,
                omniship_command=omniship_command,
                pipeline_config=pipeline_config,
            )
            if stage == Stage.BUILD:
                build_jobs = stage_jobs
            root_needs = [f"{stage.value}-complete"]

        document: dict[str, object] = {
            "workflow": {
                "rules": [
                    {
                        "if": "$CI_COMMIT_TAG",
                        "variables": {REF_TYPE_ENV: "tag"},
                    },
                    {
                        "if": '$CI_PIPELINE_SOURCE == "merge_request_event"',
                        "variables": {REF_TYPE_ENV: "branch"},
                    },
                    {
                        "if": (
                            "$CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS && "
                            '$CI_PIPELINE_SOURCE == "push"'
                        ),
                        "when": "never",
                    },
                    {
                        "if": "$CI_COMMIT_BRANCH",
                        "variables": {REF_TYPE_ENV: "branch"},
                    },
                ]
            },
            "stages": [stage.value for stage in Stage],
            "default": {"image": gitlab.image},
            "variables": {
                # The build directory is a separate mount on GitLab runners.
                "UV_LINK_MODE": "copy",
                REF_NAME_ENV: "$CI_COMMIT_REF_NAME",
            },
            **jobs,
        }
        rendered = yaml.safe_dump(
            document,
            sort_keys=False,
            width=1000,
            allow_unicode=True,
        )
        content = (
            f"# Generated by OmniShip from {source.name}. Do not edit directly.\n"
            f"{rendered}"
        )
        return (GeneratedFile(workspace_root / CONFIG_FILENAME, content),)

    def _stage_jobs(
        self,
        stage: Stage,
        nodes: dict[str, NodeConfig],
        jobs: dict[str, dict[str, object]],
        *,
        gitlab: GitLabCI,
        root_needs: list[str],
        build_jobs: list[str],
        omniship_command: list[str],
        pipeline_config: Path,
    ) -> list[str]:
        """Add one job per node and, before Ship, the stage gate.

        Returns the node job names. Ship has no gate: nothing runs after it,
        and its jobs do not exist in pipelines for other refs.
        """

        gate = f"{stage.value}-complete"
        job_names: dict[str, str] = {}
        for node_name in nodes:
            normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", node_name).strip("-").lower()
            job_name = f"{stage.value}-{normalized}"
            if job_name in job_names.values() or job_name == gate:
                raise ValueError(
                    f"GitLab CI job name collision for node '{node_name}': {job_name}"
                )
            job_names[node_name] = job_name

        depended_on = {
            dependency for node in nodes.values() for dependency in node.needs
        }
        terminal: list[str] = []
        for node_name, node in nodes.items():
            placement = (
                placement_for(node.execution, GitLabCI.name) or gitlab.default_job
            )
            if not isinstance(placement, GitLabJob):
                raise ValueError(
                    f"Node '{node_name}' has execution metadata that is not a GitLab job"
                )
            # Fails with "Target 'gitlab/ci' cannot provision requirement ..."
            # until a plugin registers a resolver for this target.
            self.registry.resolve_requirements(self.name, node.requirements)

            command = [
                *omniship_command,
                "run-node",
                "--stage",
                stage.value,
                "--node",
                node_name,
                "--config",
                pipeline_config.as_posix(),
            ]
            needs: list[object] = [
                job_names[dependency] for dependency in node.needs
            ] or list(root_needs)
            # GitLab downloads the artifacts of every job in ``needs``, so a
            # Build job already has its Build dependencies' bundles.
            imports_artifacts = stage == Stage.BUILD and bool(node.needs)
            if stage == Stage.SHIP and build_jobs:
                needs.extend({"job": name, "artifacts": True} for name in build_jobs)
                imports_artifacts = True
            if imports_artifacts:
                command.extend(["--import-artifacts-root", ARTIFACT_PATH])
            export_path = f"{ARTIFACT_PATH}/{job_names[node_name]}"
            if stage == Stage.BUILD:
                command.extend(["--export-artifacts", export_path])

            job: dict[str, object] = {"stage": stage.value}
            if placement.image is not None:
                job["image"] = placement.image
            if placement.tags:
                job["tags"] = list(placement.tags)
            if placement.timeout_minutes is not None:
                job["timeout"] = f"{placement.timeout_minutes}m"
            if stage == Stage.SHIP:
                job["rules"] = [{"if": SHIP_RULE}]
            if needs:
                job["needs"] = needs
            variables = {
                name: _variable_document(value)
                for name, value in placement.variables.items()
                if not (isinstance(value, SecretRef) and value.name == name)
            }
            if stage in {Stage.BUILD, Stage.SHIP}:
                variables[REVISION_ENV] = "$CI_COMMIT_SHA"
            if imports_artifacts:
                # Reject bundles that another commit's pipeline produced.
                variables[EXPECTED_ARTIFACT_REVISION_ENV] = "$CI_COMMIT_SHA"
            if variables:
                job["variables"] = variables
            job["script"] = [shlex.join(command)]
            if stage == Stage.BUILD:
                job["artifacts"] = {"paths": [export_path]}
            jobs[job_names[node_name]] = job
            if node_name not in depended_on:
                terminal.append(job_names[node_name])

        if stage == Stage.SHIP:
            return list(job_names.values())
        gate_needs: list[object] = list(terminal or root_needs)
        if stage == Stage.BUILD and terminal:
            # The gate only orders jobs; skip downloading the bundles.
            gate_needs = [{"job": name, "artifacts": False} for name in terminal]
        jobs[gate] = {
            "stage": stage.value,
            "needs": gate_needs,
            "script": [f"echo '{stage.value} stage complete'"],
        }
        return list(job_names.values())
