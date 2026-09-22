import asyncio
import os
import time

from pydantic import BaseModel, ConfigDict, Field

from omniship.core.context import ExecutionContext
from omniship.core.execution import IdentityToken
from omniship.core.node import NodeInputs
from omniship.core.result import NodeResult, NodeStatus
from omniship.core.stage import Stage
from omniship.plugins.metadata import OperationDefinition
from omniship.plugins.registry import PluginRegistry
from omniship.runtime import TaskContext

from .actions import (
    GitHubActionsGenerator,
    GitHubCheckout,
    GitHubPermission,
    GitHubPermissions,
    GitHubWorkflowArtifacts,
)
from .runtime import GitHub


class GithubPagesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: str = Field(
        default="site",
        min_length=1,
        description="Name of the directory artifact containing the static site",
    )


class GithubExternalWorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uses: str
    inputs: dict[str, str | int | float | bool] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    permissions: dict[str, str] = Field(default_factory=dict)


class GithubExternalWorkflowOperation:
    name: str = "github/external-workflow"
    stages: frozenset[Stage] = frozenset(Stage)
    cacheable: bool = False

    async def execute(
        self,
        context: ExecutionContext,
        inputs: NodeInputs,
    ) -> NodeResult:
        return NodeResult(
            status=NodeStatus.FAILED,
            error_message=(
                "External reusable workflows require GitHub Actions; "
                "they cannot run through OmniShip locally"
            ),
        )


class GithubPagesOperation:
    name: str = "github/pages"
    stages: frozenset[Stage] = frozenset({Stage.SHIP})
    cacheable: bool = False

    async def execute(
        self,
        context: ExecutionContext,
        inputs: NodeInputs,
    ) -> NodeResult:
        start_time = time.monotonic()
        try:
            cfg = GithubPagesConfig.model_validate(inputs.params)
        except Exception as exc:
            return NodeResult(
                status=NodeStatus.FAILED,
                duration=time.monotonic() - start_time,
                error_message=f"Invalid github/pages configuration: {exc}",
            )

        task_context = TaskContext(
            context.workspace_root,
            {**os.environ, **context.env},
            context.artifacts.to_list(),
            context.inputs,
            context.emit_log,
            host=context.host,
        )
        try:
            pages = await asyncio.to_thread(
                GitHub(task_context).pages,
                artifact=cfg.artifact,
            )
        except Exception as exc:
            return NodeResult(
                status=NodeStatus.FAILED,
                duration=time.monotonic() - start_time,
                error_message=str(exc),
            )
        return NodeResult(
            status=NodeStatus.SUCCESS,
            duration=time.monotonic() - start_time,
            stdout="\n".join(task_context.log.lines),
            outputs={
                "artifact": pages.artifact,
                "path": str(pages.path),
            },
        )


class GithubReleaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(..., description="GitHub repository in 'owner/repo' format")
    tag: str = Field(..., description="Git release tag name, e.g. 'v1.0.0'")
    title: str | None = Field(default=None, description="Release title")
    body: str | None = Field(default=None, description="Release notes / description")
    generate_notes: bool = Field(
        default=False,
        description="Ask GitHub to generate release notes",
    )
    draft: bool = Field(default=False, description="Whether to create as draft")
    prerelease: bool = Field(default=False, description="Whether this is a prerelease")
    files: list[str] = Field(
        default_factory=list,
        description="Explicit artifact names or paths to attach; defaults to all context artifacts",
    )
    dry_run: bool = Field(
        default=False,
        description="Dry run mode: simulate release creation and artifact upload",
    )


class GithubTagConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(..., description="GitHub repository in owner/repo form")
    tag: str = Field(..., min_length=1, description="Git tag name")
    target: str | None = Field(default=None, description="Git object SHA")
    force: bool = Field(default=False, description="Replace an existing tag")
    dry_run: bool = Field(default=False, description="Simulate tag creation")


class GithubTagOperation:
    name: str = "github/tag"
    stages: frozenset[Stage] = frozenset({Stage.SHIP})
    cacheable: bool = False

    async def execute(
        self,
        context: ExecutionContext,
        inputs: NodeInputs,
    ) -> NodeResult:
        start_time = time.monotonic()
        try:
            cfg = GithubTagConfig.model_validate(inputs.params)
        except Exception as exc:
            return NodeResult(
                status=NodeStatus.FAILED,
                duration=time.monotonic() - start_time,
                error_message=f"Invalid github/tag configuration: {exc}",
            )
        task_context = TaskContext(
            context.workspace_root,
            {**os.environ, **context.env},
            context.artifacts.to_list(),
            context.inputs,
            context.emit_log,
            host=context.host,
        )
        try:
            result = await asyncio.to_thread(
                GitHub(task_context).tag,
                repository=cfg.repository,
                tag=cfg.tag,
                target=cfg.target,
                force=cfg.force,
                dry_run=cfg.dry_run,
            )
        except Exception as exc:
            return NodeResult(
                status=NodeStatus.FAILED,
                duration=time.monotonic() - start_time,
                error_message=str(exc),
            )
        return NodeResult(
            status=NodeStatus.SUCCESS,
            duration=time.monotonic() - start_time,
            stdout="\n".join(task_context.log.lines),
            outputs={
                "repository": result.repository,
                "tag": result.tag,
                "target": result.target,
            },
        )


class GithubReleaseOperation:
    name: str = "github/release"
    stages: frozenset[Stage] = frozenset({Stage.SHIP})
    cacheable: bool = False

    async def execute(
        self,
        context: ExecutionContext,
        inputs: NodeInputs,
    ) -> NodeResult:
        start_time = time.monotonic()
        try:
            cfg = GithubReleaseConfig.model_validate(inputs.params)
        except Exception as e:
            return NodeResult(
                status=NodeStatus.FAILED,
                duration=time.monotonic() - start_time,
                error_message=f"Invalid github/release configuration: {e}",
            )

        task_context = TaskContext(
            context.workspace_root,
            {**os.environ, **context.env},
            context.artifacts.to_list(),
            context.inputs,
            context.emit_log,
            host=context.host,
        )
        notes = "auto" if cfg.generate_notes else cfg.body
        try:
            release = await asyncio.to_thread(
                GitHub(task_context).release,
                repository=cfg.repository,
                tag=cfg.tag,
                notes=notes,
                title=cfg.title,
                draft=cfg.draft,
                prerelease=cfg.prerelease,
                files=tuple(cfg.files),
                dry_run=cfg.dry_run,
            )
        except Exception as exc:
            return NodeResult(
                status=NodeStatus.FAILED,
                duration=time.monotonic() - start_time,
                error_message=str(exc),
            )
        return NodeResult(
            status=NodeStatus.SUCCESS,
            duration=time.monotonic() - start_time,
            stdout="\n".join(task_context.log.lines),
            outputs={
                "repository": release.repository,
                "tag": release.tag,
                "release_url": release.release_url,
                "uploaded_count": release.uploaded_count,
            },
        )


def get_github_definition() -> OperationDefinition:
    return OperationDefinition(
        name=GithubReleaseOperation.name,
        stages=GithubReleaseOperation.stages,
        description="Publish release and upload built artifacts to GitHub",
        config_model=GithubReleaseConfig,
        cacheable=False,
    )


def get_github_pages_definition() -> OperationDefinition:
    return OperationDefinition(
        name=GithubPagesOperation.name,
        stages=GithubPagesOperation.stages,
        description="Prepare a static site for deployment to GitHub Pages",
        config_model=GithubPagesConfig,
        cacheable=False,
    )


def get_github_tag_definition() -> OperationDefinition:
    return OperationDefinition(
        name=GithubTagOperation.name,
        stages=GithubTagOperation.stages,
        description="Create or update a GitHub tag",
        config_model=GithubTagConfig,
        cacheable=False,
    )


def register_github_plugin(registry: PluginRegistry) -> None:
    registry.register_operation(
        GithubExternalWorkflowOperation(),
        OperationDefinition(
            name=GithubExternalWorkflowOperation.name,
            stages=GithubExternalWorkflowOperation.stages,
            description="Call an external GitHub reusable workflow as one job",
            config_model=GithubExternalWorkflowConfig,
            cacheable=False,
        ),
    )
    registry.register_operation(
        GithubPagesOperation(),
        get_github_pages_definition(),
    )
    registry.register_operation(
        GithubReleaseOperation(),
        get_github_definition(),
    )
    registry.register_operation(
        GithubTagOperation(),
        get_github_tag_definition(),
    )
    registry.register_requirement_resolver(
        "github/actions",
        GitHubCheckout,
        lambda requirement: requirement,
    )
    registry.register_requirement_resolver(
        "github/actions",
        GitHubWorkflowArtifacts,
        lambda requirement: requirement,
    )
    registry.register_requirement_resolver(
        "github/actions",
        IdentityToken,
        lambda requirement: GitHubPermissions(
            contents=GitHubPermission.READ,
            id_token=GitHubPermission.WRITE,
        ),
    )
    registry.register_workflow_generator(GitHubActionsGenerator(registry))
