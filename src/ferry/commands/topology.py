from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..auth import resolve_credentials
from ..config import ConfigError, NodeConfigMissing, load_config
from ..executors import ExecError, SshExecutor, resolve_remote_ferry_key
from ..nodes import resolve_target
from ..paths import identity_path
from ..topology import Edges, RoutePolicy, TopologyError, probe_edges

console = Console()


def topology(
    target: str | None = typer.Argument(None, help="target alias (default: all targets)"),
    refresh: bool = typer.Option(False, "--refresh", help="ignore the topology cache"),
    ask_pass: bool = typer.Option(
        False, "--ask-pass", help="prompt for SSH passwords (not stored)"
    ),
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

    credentials = resolve_credentials(ask=ask_pass)
    passwords: dict[str, str] = {}
    if credentials.build_password:
        passwords["build"] = credentials.build_password
    if credentials.target_password:
        passwords["deploy"] = credentials.target_password
    identity = str(identity_path()) if identity_path().is_file() else None
    executor = SshExecutor(passwords=passwords, identity=identity)

    table = Table(title=f"ferry topology: {project}")
    table.add_column("target")
    table.add_column("dev->build")
    table.add_column("build->deploy")
    table.add_column("dev->deploy")
    table.add_column("route")

    for name in names:
        cfg = targets[name]
        try:
            resolved = resolve_target(cfg)
        except NodeConfigMissing as exc:
            table.add_row(name, "-", "-", "-", f"error: {exc}")
            continue
        try:
            dev_build = executor.check(resolved.build)
            build_key = (
                resolve_remote_ferry_key(executor, resolved.build)
                if dev_build
                else None
            )
            if resolved.deploy_role == "dev":
                edges = Edges(dev_build=dev_build, build_target=False, dev_target=True)
                route = "build->dev (local deploy)"
            elif resolved.deploy_role == "build":
                edges = Edges(dev_build=dev_build, build_target=False, dev_target=False)
                route = "build->build (local deploy)"
            else:
                prefer_via_dev = (
                    bool(credentials.target_password)
                    or resolved.transfer_from == "dev"
                )
                edges = probe_edges(
                    executor,
                    resolved.build,
                    resolved.deploy,
                    project=project,
                    target_name=name,
                    refresh=refresh,
                    skip_build_target=prefer_via_dev,
                    build_key=build_key,
                )
                if resolved.transfer_from == "build" and not edges.build_target:
                    route = "error: transfer_from=build.node but build cannot reach deploy"
                elif resolved.transfer_from == "dev" and not edges.dev_target:
                    route = "error: transfer_from=dev.node but dev cannot reach deploy"
                else:
                    try:
                        route = (
                            RoutePolicy()
                            .decide(edges, prefer_via_dev=prefer_via_dev)
                            .name
                        )
                    except TopologyError as exc:
                        route = f"error: {exc}"
        except ExecError as exc:
            edges = None
            route = f"error: {exc}"
        table.add_row(
            name,
            _mark(edges.dev_build if edges else None),
            _mark(edges.build_target if edges else None),
            _mark(edges.dev_target if edges else None),
            route,
        )
    console.print(table)


def _mark(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "[green]ok[/green]" if value else "[red]no[/red]"
