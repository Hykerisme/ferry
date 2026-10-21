from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from ..config import (
    CONFIG_FILENAME,
    BuildCfg,
    DeployCfg,
    FerryConfig,
    ProjectCfg,
    TargetCfg,
    config_to_yaml,
    diff_config,
    initial_config,
)
from ..private_config import ensure_gitignore

console = Console()


def _local_target(root: Path) -> TargetCfg:
    if (root / "CMakeLists.txt").is_file():
        out = "./build/out"
        run_build = "cmake -B build && cmake --build build -j"
    elif (root / "Makefile").is_file():
        out = "./dist"
        run_build = "make -j"
    else:
        out = "./dist"
        run_build = 'echo "edit run_build to produce ./dist"'
    return TargetCfg(
        build=BuildCfg(
            node="dev.node",
            workdir=".",
            out=out,
            run_build=run_build,
        ),
        deploy=DeployCfg(
            node="dev.node",
            workdir="./deploy",
            run_deploy="echo deployed",
        ),
    )


def init(
    force: bool = typer.Option(False, "--force", "-f", help="overwrite an existing config"),
    local: bool = typer.Option(
        False, "--local", help="create a single-machine target (dev.node)"
    ),
) -> None:
    """Create a .ferry.yaml in the current directory."""
    root = Path.cwd().resolve()
    path = root / CONFIG_FILENAME

    for parent in root.parents:
        if (parent / CONFIG_FILENAME).is_file() and parent != root:
            console.print(
                f"[yellow]warning:[/yellow] a Ferry project already exists at {parent}; "
                "nested projects use the nearest config"
            )
            break

    if path.exists() and not force:
        console.print(
            f"[red]{CONFIG_FILENAME} already exists[/red]; use --force to overwrite it"
        )
        raise typer.Exit(1)

    if local:
        config = FerryConfig(
            project=ProjectCfg(name=root.name),
            targets={"local": _local_target(root)},
        )
    else:
        config = initial_config(root.name)
    new_text = config_to_yaml(config)
    if path.exists():
        old_text = path.read_text(encoding="utf-8")
        diff = diff_config(old_text, new_text)
        if diff:
            console.print(diff, markup=False, highlight=False)
    path.write_text(new_text, encoding="utf-8")
    added = ensure_gitignore(
        root, extra=(".ferry/", "deploy/") if local else ()
    )
    if added:
        console.print(f"added to .gitignore: {', '.join(added)}")
    console.print(f"created {path}")
    if local:
        console.print("next: edit run_build/out, then run 'ferry run local'")
    else:
        console.print("next: run 'ferry up' to define the build and deploy nodes")
