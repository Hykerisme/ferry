from __future__ import annotations

import typer
from rich.console import Console

from ..auth import resolve_credentials
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
    ask_pass: bool = typer.Option(
        False, "--ask-pass", help="prompt for SSH passwords (not stored)"
    ),
) -> None:
    """Print the full command sequence without building or deploying."""
    try:
        loaded = load_config()
        credentials = resolve_credentials(ask=ask_pass)
        pipeline = Pipeline(
            loaded, target, console=console, credentials=credentials
        )
        ctx = pipeline.prepare(refresh_topology=refresh_topology)
        steps = pipeline.build_steps(via_dev=ctx.route.uses_dev_relay)
    except (ConfigError, PipelineError, ProbeError, ExecError) as exc:
        console.print(f"[red]plan failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    render_plan(console, ctx, steps)
