import os
from collections.abc import Iterable
from datetime import datetime

from rich.console import Console
from rich.text import Text

from omniship.core.graph import StageGraph
from omniship.core.logging import (
    Logging,
    LogLevel,
    LogRecord,
    LogStream,
    includes_level,
)
from omniship.core.node import Node
from omniship.core.result import NodeResult, NodeStatus
from omniship.core.stage import Stage
from omniship.plugins.api import LogAnnotator


class TerminalUI:
    def __init__(
        self,
        console: Console | None = None,
        logging: Logging | None = None,
        annotators: Iterable[LogAnnotator] = (),
    ) -> None:
        self.console = console or Console()
        self.logging = logging or Logging()
        self._annotator = next(
            (annotator for annotator in annotators if annotator.active(os.environ)),
            None,
        )
        self._current_stage: Stage | None = None
        self._stage_failed = False
        self._printed_stages: set[Stage] = set()
        self._streamed_lines: dict[tuple[Stage, str], list[str]] = {}
        self._unicode_symbols = self._supports_unicode_symbols()

    def on_stage_start(self, stage: Stage, graph: StageGraph) -> None:
        self._current_stage = stage
        self.console.print()
        self.console.print(f"[bold cyan]{stage.value.upper()}[/bold cyan]")
        if not graph.nodes:
            self.console.print("  [dim](no nodes defined)[/dim]")

    def on_node_start(self, stage: Stage, node: Node) -> None:
        symbol = self._symbol("→", ">")
        self.console.print(
            f"  [cyan]{symbol}[/cyan] Starting [bold]{node.id}[/bold]"
        )

    def emit(self, record: LogRecord) -> None:
        self._streamed_lines.setdefault((record.stage, record.task), []).append(
            record.message
        )
        if not includes_level(self.logging.level, record.level):
            return
        if record.stream != LogStream.LOG and not self.logging.show_output:
            return
        timestamp = ""
        if self.logging.timestamps:
            timestamp = (
                f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] "
            )
        style = {
            LogLevel.DEBUG: "dim",
            LogLevel.INFO: "",
            LogLevel.WARNING: "yellow",
            LogLevel.ERROR: "red",
        }[record.level]
        if record.stream == LogStream.STDERR and not style:
            style = "red"
        self.console.print(
            Text(
                f"    [{record.task}] {timestamp}{record.message}",
                style=style,
            )
        )
        if self._annotator is not None and record.level in {
            LogLevel.WARNING,
            LogLevel.ERROR,
        }:
            self.console.print(
                self._annotator.annotate(
                    record.level, record.stage, record.task, record.message
                ),
                markup=False,
            )

    def on_node_finish(self, stage: Stage, node: Node, result: NodeResult) -> None:
        if result.status == NodeStatus.SUCCESS:
            dur = f"{result.duration:.2f}s"
            symbol = self._symbol("✓", "+")
            self.console.print(
                f"  [green]{symbol}[/green] [bold]{node.id:<24}[/bold] "
                f"[dim]{dur:>8}[/dim]"
            )
        elif result.status == NodeStatus.SKIPPED:
            reason = result.error_message or "skipped"
            symbol = self._symbol("⊘", "-")
            self.console.print(
                f"  [yellow]{symbol}[/yellow] [bold]{node.id:<24}[/bold] "
                f"[yellow]{reason}[/yellow]"
            )
        elif result.status == NodeStatus.FAILED:
            dur = f"{result.duration:.2f}s"
            symbol = self._symbol("✗", "x")
            self.console.print(
                f"  [red]{symbol}[/red] [bold]{node.id:<24}[/bold] "
                f"[red]{dur:>8}[/red]"
            )
            if result.error_message:
                self.console.print(f"    [red]Error:[/red] {result.error_message}")
                if self._annotator is not None:
                    self.console.print(
                        self._annotator.annotate(
                            LogLevel.ERROR, stage, node.id, result.error_message
                        ),
                        markup=False,
                    )
        if self.logging.show_output and includes_level(
            self.logging.level,
            LogLevel.INFO,
        ):
            self._print_result_output(stage, node.id, result)

    def on_stage_finish(self, stage: Stage, success: bool) -> None:
        if not success:
            self._stage_failed = True
            self.console.print(f"\n[bold red]{stage.value.upper()} FAILED[/bold red]")
        else:
            self.console.print(
                f"[bold green]{stage.value.upper()} COMPLETE[/bold green]"
            )

    def _print_result_output(
        self,
        stage: Stage,
        task: str,
        result: NodeResult,
    ) -> None:
        streamed = list(self._streamed_lines.get((stage, task), ()))

        def already_streamed(line: str) -> bool:
            try:
                streamed.remove(line)
            except ValueError:
                return False
            return True

        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                if not already_streamed(line):
                    self.console.print(Text(f"    [{task}] {line}"))
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                if not already_streamed(line):
                    self.console.print(Text(f"    [{task}] {line}", style="red"))

    def _supports_unicode_symbols(self) -> bool:
        encoding = self.console.encoding or "utf-8"
        try:
            "→✓⊘✗•".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            return False
        return True

    def _symbol(self, unicode_symbol: str, ascii_symbol: str) -> str:
        return unicode_symbol if self._unicode_symbols else ascii_symbol

    def print_artifacts(self, artifacts: list) -> None:
        if artifacts:
            self.console.print()
            self.console.print("[bold]Artifacts[/bold]")
            for art in artifacts:
                symbol = self._symbol("•", "*")
                self.console.print(
                    f"  [green]{symbol}[/green] {art.name} [dim]({art.path})[/dim]"
                )
