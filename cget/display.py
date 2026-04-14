from rich.console import Console
from rich.progress import (
    Progress, BarColumn, DownloadColumn, TransferSpeedColumn,
    TimeRemainingColumn, TextColumn, SpinnerColumn, TotalFileSizeColumn,
    FileSizeColumn
)
from rich.prompt import Confirm
from rich.table import Table
from rich.theme import Theme

theme = Theme({
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "bold red",
    "package": "bold cyan",
    "phase": "bold blue",
})

console = Console(theme=theme, highlight=False)


def success(msg):
    console.print("[success]\u2713[/] {}".format(msg))


def error(msg):
    console.print("[error]\u2717 {}[/]".format(msg))


def warning(msg):
    console.print("[warning]! {}[/]".format(msg))


def info(msg):
    console.print("[info]\u25cf[/] {}".format(msg))


def phase(msg):
    console.print("  [phase]\u25b8 {}[/]".format(msg))


def verbose(msg):
    console.print("  [dim]$ {}[/]".format(msg))


def pkg(name):
    return "[package]{}[/]".format(name)


def status(msg):
    return console.status("[bold]{}[/]".format(msg), spinner="dots")


class ExtractProgress:
    """Progress bar class for archive extraction."""

    def __init__(self, filename, total: float) -> None:
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn('[progress.description]{task.fields[filename]}'),
            BarColumn(),
            TextColumn('[progress.percentage]{task.percentage:3.1f}%'),
            '•',
            FileSizeColumn(),
            'of',
            TotalFileSizeColumn(),
            '•',
            TimeRemainingColumn(),
            console=console
        )
        self.progress.start()
        self.active_task = self.progress.add_task(
            'extract', filename=filename, total=total, message='')

    def __del__(self) -> None:
        self.progress.stop()

    def update(self, advance: float):
        """Update a progress bar."""
        self.progress.update(self.active_task, advance=advance)


class CallbackIOWrapper:  # pylint: disable=too-few-public-methods
    """Wrapper for IO operations."""

    def __init__(self, progress: ExtractProgress, inf) -> None:
        self.progress = progress
        self.inf = inf

    def read(self, size: int) -> bytes:
        """Read bytes from input stream and update progress bar."""
        buffer = self.inf.read(size)
        self.progress.update(advance=len(buffer))
        return buffer


class DownloadProgress:
    """Progress bar class for archive downloader."""

    def __init__(self, filename, total: float) -> None:
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn('[progress.description]{task.fields[filename]}'),
            BarColumn(),
            TextColumn('[progress.percentage]{task.percentage:>3.1f}%'),
            '•',
            DownloadColumn(),
            '•',
            TransferSpeedColumn(),
            '•',
            TimeRemainingColumn(),
            console=console
        )
        self.progress.start()
        self.task_id = self.progress.add_task(
            'download', filename=filename, total=total, message='')

    def __del__(self) -> None:
        self.progress.stop()

    def update(self, advance: float) -> None:
        """Update a progress bar."""
        self.progress.update(self.task_id, advance=advance)


def confirm(msg):
    return Confirm.ask("[bold]{}[/]".format(msg), default=False, console=console)


def package_table(packages):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("name", style="bold cyan")
    for name in packages:
        table.add_row(name)
    return table
