from __future__ import annotations

import typer
from rich.console import Console

from ..auth import resolve_credentials
from ..config import ConfigError, NodeConfigMissing, load_config
from ..executors import ExecError, SshExecutor
from ..nodes import resolve_target
from ..pipeline import Pipeline, PipelineError
from ..probe import ProbeError, cancel_remote
from ..state import load_state

console = Console()


def run(
    target: str = typer.Argument(..., help="target alias from .ferry.yaml"),
    cancel: bool = typer.Option(False, "--cancel", help="cooperatively cancel the active run"),
    refresh_topology: bool = typer.Option(
        False, "--refresh-topology", help="ignore the topology cache"
    ),
    reset_probe: bool = typer.Option(False, "--reset-probe", help="re-upload the probe"),
    ask_pass: bool = typer.Option(
        False, "--ask-pass", help="prompt for SSH passwords (not stored)"
    ),
) -> None:
    """Sync, build, route artifacts and deploy the selected target."""
    try:
        loaded = load_config()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    project = loaded.config.project.name or loaded.root.name

    if cancel:
        state = load_state(project, target)
        if state is None or (state.probe_pid is None and state.lock_pid is None):
            console.print(f"[yellow]no active run recorded for target {target!r}[/yellow]")
            raise typer.Exit(1)
        try:
            target_cfg = loaded.config.targets[target]
            resolved = resolve_target(target_cfg)
        except (KeyError, NodeConfigMissing) as exc:
            console.print(f"[red]cannot resolve build node:[/red] {exc}")
            raise typer.Exit(1) from exc
        credentials = resolve_credentials(ask=ask_pass, need_target=False)
        passwords = (
            {"build": credentials.build_password}
            if credentials.build_password
            else None
        )
        executor = SshExecutor(passwords=passwords)
        pids = [pid for pid in (state.probe_pid, state.lock_pid) if pid]
        cancel_remote(executor, resolved.build, pids)
        console.print(
            f"sent SIGTERM to pids {pids} on {resolved.build.display()}; "
            "the build will finish its grace period"
        )
        return

    try:
        credentials = resolve_credentials(ask=ask_pass)
        pipeline = Pipeline(
            loaded, target, console=console, credentials=credentials
        )
        ctx = pipeline.prepare(refresh_topology=refresh_topology, reset_probe=reset_probe)
        steps = pipeline.build_steps()
        pipeline.execute(ctx, steps)
    except (PipelineError, ProbeError, ExecError) as exc:
        console.print(f"[red]run failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]ok[/green] target={target} route={ctx.route.name} log={ctx.log_file}"
    )
