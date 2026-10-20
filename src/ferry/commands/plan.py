from __future__ import annotations

import typer
from rich.console import Console

from ..config import ConfigError, load_config
from ..executors import ExecError
from ..pipeline import Pipeline, PipelineError, render_plan
from ..probe import ProbeError

console = Console()


def plan(
    target: str = typer.Argument(..., help="target alias from .ferry.yaml"),
    refresh_topology: bool = typer.Option(
        False, "--refresh-topology", help="ignore the topology cache"
    ),
) -> None:
    """Print the full command sequence without building or deploying."""
    try:
        loaded = load_config()
        pipeline = Pipeline(loaded, target, console=console)
        ctx = pipeline.prepare(refresh_topology=refresh_topology)
        steps = pipeline.build_steps()
    except (ConfigError, PipelineError, ProbeError, ExecError) as exc:
        console.print(f"[red]plan failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    render_plan(console, ctx, steps)
