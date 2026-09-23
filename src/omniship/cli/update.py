from pathlib import Path

import click

from omniship.cli.generate import write_generated
from omniship.plugins.api import GeneratedFile
from omniship.plugins.github.dependencies import (
    GitHubActionLock,
    resolve_action,
    resolve_git_plugin,
)
from omniship.workflow.model import GitPluginPackage


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
        lock = GitHubActionLock.defaults()
        if path.is_file():
            current = GitHubActionLock.load(path)
            lock = lock.with_plugins(current.plugins)
            if dependency not in {None, "omniship"}:
                lock = lock.with_omniship_version(current.omniship_version)
            for pin in current.actions.values():
                expected = lock.actions.get(pin.name)
                if expected is None or (
                    pin.repository == expected.repository
                    and pin.major == expected.major
                ):
                    lock = lock.with_pin(pin)
        if dependency in {None, "plugins"}:
            refreshed = tuple(
                resolve_git_plugin(plugin)
                if isinstance(plugin, GitPluginPackage) and plugin.version is not None
                else plugin
                for plugin in lock.plugins
            )
            lock = lock.with_plugins(refreshed)
        if dependency == "plugins":
            selected = ()
            click.echo("Updated Git plugin version pins")
        elif dependency == "omniship":
            selected = ()
            click.echo(f"Updated omniship to {lock.omniship_version}")
        else:
            selected = lock.select(dependency)
        for current in selected:
            updated = resolve_action(current)
            lock = lock.with_pin(updated)
            click.echo(
                f"Updated github/{updated.repository} to {updated.version} "
                f"({updated.sha[:12]})"
            )
        write_generated(GeneratedFile(path, lock.render()))
    except Exception as exc:
        if isinstance(exc, click.ClickException):
            raise
        raise click.ClickException(str(exc)) from exc
    click.echo("Run 'omniship generate' to refresh generated workflows")
