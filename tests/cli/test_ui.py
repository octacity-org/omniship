from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path

from rich.console import Console

from omniship.cli.ui import TerminalUI
from omniship.core.artifact import Artifact
from omniship.core.logging import Logging, LogLevel, LogRecord
from omniship.core.node import Node
from omniship.core.result import NodeResult, NodeStatus
from omniship.core.stage import Stage
from omniship.plugins.github.console import GitHubLogAnnotator


def test_terminal_ui_hides_debug_logs_by_default() -> None:
    output = StringIO()
    ui = TerminalUI(console=Console(file=output, force_terminal=False))

    ui.emit(LogRecord(Stage.CHECK, "lint", LogLevel.DEBUG, "details"))
    ui.emit(LogRecord(Stage.CHECK, "lint", LogLevel.INFO, "visible"))

    assert "details" not in output.getvalue()
    assert "[lint] visible" in output.getvalue()


def test_terminal_ui_renders_github_warning_annotations(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    output = StringIO()
    ui = TerminalUI(
        console=Console(file=output, force_terminal=False),
        logging=Logging(level=LogLevel.WARNING),
        annotators=[GitHubLogAnnotator()],
    )

    ui.emit(LogRecord(Stage.BUILD, "package", LogLevel.WARNING, "Check output"))

    assert "::warning title=Build · Package::Check output" in output.getvalue()


def test_terminal_ui_renders_no_annotations_without_an_annotator(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    output = StringIO()
    ui = TerminalUI(console=Console(file=output, force_terminal=False))

    ui.emit(LogRecord(Stage.BUILD, "package", LogLevel.WARNING, "Check output"))

    assert "Check output" in output.getvalue()
    assert "::warning" not in output.getvalue()


def test_terminal_ui_skips_inactive_annotators(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    output = StringIO()
    ui = TerminalUI(
        console=Console(file=output, force_terminal=False),
        annotators=[GitHubLogAnnotator()],
    )

    ui.emit(LogRecord(Stage.BUILD, "package", LogLevel.WARNING, "Check output"))

    assert "::warning" not in output.getvalue()


def test_terminal_ui_uses_safe_symbols_for_legacy_windows_encoding(
    tmp_path: Path,
) -> None:
    raw_output = BytesIO()
    output = TextIOWrapper(raw_output, encoding="cp1252", errors="strict")
    ui = TerminalUI(console=Console(file=output, force_terminal=False))
    node = Node("pytest", Stage.CHECK, "python/pytest")
    artifact_path = tmp_path / "package.whl"
    artifact_path.write_bytes(b"wheel")

    ui.on_node_start(Stage.CHECK, node)
    ui.on_node_finish(
        Stage.CHECK,
        node,
        NodeResult(NodeStatus.SUCCESS, duration=0.1),
    )
    ui.on_node_finish(
        Stage.CHECK,
        node,
        NodeResult(NodeStatus.SKIPPED, error_message="dependency failed"),
    )
    ui.on_node_finish(
        Stage.CHECK,
        node,
        NodeResult(NodeStatus.FAILED, duration=0.1),
    )
    ui.print_artifacts([Artifact.from_path(artifact_path)])
    output.flush()

    rendered = raw_output.getvalue().decode("cp1252")
    assert "> Starting pytest" in rendered
    assert "+ pytest" in rendered
    assert "- pytest" in rendered
    assert "x pytest" in rendered
    assert "* package.whl" in rendered
