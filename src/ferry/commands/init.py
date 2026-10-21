from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from ..config import (
    CONFIG_FILENAME,
    config_to_yaml,
    diff_config,
    initial_config,
)
from ..private_config import ensure_gitignore

console = Console()


def init(
    force: bool = typer.Option(False, "--force", "-f", help="overwrite an existing config"),
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

    new_text = config_to_yaml(initial_config(root.name))
    if path.exists():
        old_text = path.read_text(encoding="utf-8")
        diff = diff_config(old_text, new_text)
        if diff:
            console.print(diff, markup=False, highlight=False)
    path.write_text(new_text, encoding="utf-8")
    added = ensure_gitignore(root)
    if added:
        console.print(f"added to .gitignore: {', '.join(added)}")
    console.print(f"created {path}")
    console.print("next: run 'ferry up' to define the build and deploy nodes")
