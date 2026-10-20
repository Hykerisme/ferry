from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..config import ConfigError, load_config
from ..state import load_state

console = Console()


def status(
    target: str | None = typer.Argument(None, help="target alias (default: all targets)"),
) -> None:
    """Show the last recorded run for one or all targets."""
    try:
        loaded = load_config()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    project = loaded.config.project.name or loaded.root.name
    names = [target] if target else sorted(loaded.config.targets)
    if target and target not in loaded.config.targets:
        console.print(f"[red]unknown target {target!r}[/red]")
        raise typer.Exit(1)

    summary = Table(title=f"ferry status: {project}")
    summary.add_column("target")
    summary.add_column("run")
    summary.add_column("commit")
    summary.add_column("dirty")
    summary.add_column("topology")
    summary.add_column("updated")

    stage_table = Table(title="stages")
    stage_table.add_column("target")
    stage_table.add_column("stage")
    stage_table.add_column("state")
    stage_table.add_column("duration")

    found = False
    for name in names:
        state = load_state(project, name)
        if state is None:
            summary.add_row(name, "(no runs)", "-", "-", "-", "-")
            continue
        found = True
        summary.add_row(
            name,
            state.run_id,
            state.commit or "-",
            "yes" if state.dirty else "no" if state.dirty is not None else "-",
            state.topology or "-",
            state.updated_at,
        )
        for stage, stage_state in state.stages.items():
            stage_table.add_row(name, stage, stage_state.state, stage_state.dur or "-")

    console.print(summary)
    if found:
        console.print(stage_table)
    if target:
        state = load_state(project, target)
        if state and state.log:
            console.print(f"log: {state.log}")
