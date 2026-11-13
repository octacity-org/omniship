"""GitHub Actions workflow commands for warnings and errors in job logs."""

from collections.abc import Mapping

from omniship.core.logging import LogLevel
from omniship.core.stage import Stage


class GitHubLogAnnotator:
    """Render warnings and errors as GitHub Actions annotations."""

    name = "github/actions"

    def active(self, env: Mapping[str, str]) -> bool:
        return env.get("GITHUB_ACTIONS") == "true"

    def annotate(
        self,
        level: LogLevel,
        stage: Stage,
        task: str,
        message: str,
    ) -> str:
        command = "warning" if level == LogLevel.WARNING else "error"
        title = _escape_property(_title(stage, task))
        return f"::{command} title={title}::{_escape_data(message)}"


def _title(stage: Stage, task: str) -> str:
    words = task.replace("_", "-").split("-")
    name = " ".join(
        "GitHub" if word.casefold() == "github" else word.title() for word in words
    )
    return f"{stage.value.title()} · {name}"


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")
