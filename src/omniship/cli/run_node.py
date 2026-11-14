import sys

import click

from omniship.cli.runner import run_node
from omniship.core.stage import Stage


@click.command("run-node", hidden=True)
@click.option(
    "--stage",
    type=click.Choice([stage.value for stage in Stage]),
    required=True,
)
@click.option("--node", "node_id", required=True)
@click.option("-c", "--config", "config_path", help="Path to configuration file")
@click.option(
    "--working-directory",
    help="Workspace subdirectory used by the selected task",
)
@click.option(
    "--import-artifacts-root",
    "import_artifacts_roots",
    multiple=True,
    help="Import every artifact bundle found below this directory (repeatable)",
)
@click.option(
    "--export-artifacts",
    "artifact_export_path",
    help="Export artifacts produced by this node",
)
def run_node_cmd(
    stage: str,
    node_id: str,
    config_path: str | None,
    working_directory: str | None,
    import_artifacts_roots: tuple[str, ...],
    artifact_export_path: str | None,
) -> None:
    """Run one generated DAG node. Intended for CI providers."""
    code = run_node(
        Stage(stage),
        node_id,
        config_path=config_path,
        working_directory=working_directory,
        artifact_import_root=import_artifacts_roots,
        artifact_export_path=artifact_export_path,
    )
    if code != 0:
        sys.exit(code)
