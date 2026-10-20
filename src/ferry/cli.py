from __future__ import annotations

import typer

from . import __version__
from .commands import init, plan, run, status, topology, up


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ferry {__version__}")
        raise typer.Exit()


app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    help=(
        "Ferry: send a probe to the build node, run user scripts per stage, "
        "and route artifacts by SSH reachability."
    ),
)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="show the Ferry version and exit",
    ),
) -> None:
    """Build-deploy orchestration over plain SSH."""

app.command("init")(init.init)
app.command("up")(up.up)
app.command("run")(run.run)
app.command("plan")(plan.plan)
app.command("status")(status.status)
app.command("topology")(topology.topology)
