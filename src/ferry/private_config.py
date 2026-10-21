from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .config import CONFIG_FILENAME, PRIVATE_CONFIG_FILENAME


def yaml_roundtrip() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    return yaml


def load_document(path: Path) -> Any:
    yaml = yaml_roundtrip()
    with path.open("r", encoding="utf-8") as handle:
        return yaml.load(handle)


def dump_document(document: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    yaml = yaml_roundtrip()
    with temporary.open("w", encoding="utf-8") as handle:
        yaml.dump(document, handle)
    os.replace(temporary, path)


def regenerate_public(private_path: Path, public_path: Path) -> None:
    """Regenerate .ferry.yaml from the private config, stripping node definitions."""
    if not private_path.is_file():
        return
    document = load_document(private_path)
    if document is None:
        return
    targets = document.get("targets") or {}
    for target in targets.values():
        build = target.get("build") or {}
        build.pop("node", None)
        deploy = target.get("deploy") or {}
        deploy.pop("node", None)
        deploy.pop("transfer_from", None)
    dump_document(document, public_path)


def ensure_gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    existing = []
    if path.is_file():
        existing = path.read_text(encoding="utf-8").splitlines()
    missing = [
        entry
        for entry in (PRIVATE_CONFIG_FILENAME, ".env")
        if entry not in existing
    ]
    if missing:
        with path.open("a", encoding="utf-8") as handle:
            if existing and not existing[-1].strip():
                pass
            elif existing:
                handle.write("\n")
            handle.write("\n".join(missing) + "\n")
    return missing


def target_node_block(document: Any, target_name: str) -> Any:
    targets = document.get("targets") or {}
    if target_name not in targets:
        raise KeyError(target_name)
    return targets[target_name]


__all__ = [
    "CONFIG_FILENAME",
    "PRIVATE_CONFIG_FILENAME",
    "dump_document",
    "ensure_gitignore",
    "load_document",
    "regenerate_public",
    "target_node_block",
    "yaml_roundtrip",
]
