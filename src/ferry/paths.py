from __future__ import annotations

import os
import re
from pathlib import Path


def ferry_home() -> Path:
    raw = os.environ.get("FERRY_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".ferry"


def bin_dir() -> Path:
    return ferry_home() / "bin"


def state_dir() -> Path:
    return ferry_home() / "state"


def logs_dir() -> Path:
    return ferry_home() / "logs"


def cache_dir() -> Path:
    return ferry_home() / "cache"


def ensure_home() -> None:
    for directory in (bin_dir(), state_dir(), logs_dir(), cache_dir()):
        directory.mkdir(parents=True, exist_ok=True)


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "project"
