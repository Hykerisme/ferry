from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .paths import logs_dir, safe_name, state_dir


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class StageState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ok", "failed", "running", "interrupted"]
    dur: str | None = None
    since: str | None = None


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    target: str
    run_id: str
    commit: str | None = None
    dirty: bool | None = None
    toolchain: str | None = None
    topology: str | None = None
    stages: dict[str, StageState] = Field(default_factory=dict)
    log: str | None = None
    updated_at: str = Field(default_factory=utc_now)
    build_node: str | None = None
    lock_pid: int | None = None
    probe_pid: int | None = None


def state_path(project: str, target: str) -> Path:
    return state_dir() / safe_name(project) / f"{safe_name(target)}.json"


def log_path(project: str, target: str, run_id: str) -> Path:
    directory = logs_dir() / safe_name(project)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe_name(target)}-{run_id}.log"


def load_state(project: str, target: str) -> RunState | None:
    path = state_path(project, target)
    if not path.is_file():
        return None
    try:
        return RunState.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def save_state(state: RunState) -> Path:
    state.updated_at = utc_now()
    path = state_path(state.project, state.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=False)
    directory = str(path.parent)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp"
    ) as handle:
        handle.write(payload)
        handle.write("\n")
        temp_path = handle.name
    os.replace(temp_path, path)
    return path


def git_info(root: Path) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    commit_text = commit.stdout.strip() if commit.returncode == 0 else None
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    return commit_text or None, dirty
