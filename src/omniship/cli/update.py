from pathlib import Path

import click

from omniship.cli.generate import write_generated
from omniship.plugins.discovery import load_plugins


@click.command("update")
@click.argument("dependency", required=False)
@click.option(
    "--lock-file",
    default="omniship.lock",
    show_default=True,
    help="OmniShip dependency lock file",
)
def update_cmd(dependency: str | None, lock_file: str) -> None:
    """Update locked workflow dependencies."""
    path = Path(lock_file).resolve()
    try:
        updaters = load_plugins().list_lock_updaters()
        if not updaters:
            raise click.ClickException(
                "No installed plugin manages workflow dependency locks"
            )
        for updater in updaters:
            write_generated(updater.update(path, dependency, click.echo))
    except Exception as exc:
        if isinstance(exc, click.ClickException):
            raise
        raise click.ClickException(str(exc)) from exc
    click.echo("Run 'omniship generate' to refresh generated workflows")
