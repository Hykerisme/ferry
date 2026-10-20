from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..config import ConfigError, load_config
from ..executors import ExecError, SshExecutor
from ..topology import RoutePolicy, TopologyError, probe_edges

console = Console()


def topology(
    target: str | None = typer.Argument(None, help="target alias (default: all targets)"),
    refresh: bool = typer.Option(False, "--refresh", help="ignore the topology cache"),
) -> None:
    """Probe SSH reachability for the three edges and print the routing decision."""
    try:
        loaded = load_config()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    project = loaded.config.project.name or loaded.root.name
    targets = loaded.config.targets
    names = [target] if target else sorted(targets)
    if target and target not in targets:
        console.print(f"[red]unknown target {target!r}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"ferry topology: {project}")
    table.add_column("target")
    table.add_column("dev->build")
    table.add_column("build->target")
    table.add_column("dev->target")
    table.add_column("route")

    executor = SshExecutor()
    for name in names:
        cfg = targets[name]
        try:
            edges = probe_edges(
                executor,
                cfg.build.node,
                cfg.deploy.node,
                project=project,
                target=name,
                refresh=refresh,
            )
            try:
                route = RoutePolicy().decide(edges).name
            except TopologyError as exc:
                route = f"error: {exc}"
        except ExecError as exc:
            edges = None
            route = f"error: {exc}"
        table.add_row(
            name,
            _mark(edges.dev_build if edges else False),
            _mark(edges.build_target if edges else False),
            _mark(edges.dev_target if edges else False),
            route,
        )
    console.print(table)


def _mark(value: bool) -> str:
    return "[green]ok[/green]" if value else "[red]no[/red]"
