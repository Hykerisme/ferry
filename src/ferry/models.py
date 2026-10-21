from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Where = Literal["dev", "build", "target"]


class NodeInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    os: str = ""
    arch: str = ""
    kernel: str = ""
    distro: str = ""
    os_ver: str = ""
    init: str = ""
    glibc: str = ""
    has_tar: bool = False
    has_gzip: bool = False
    has_rsync: bool = False
    has_scp: bool = False


class ScriptResult(BaseModel):
    state: Literal["unset", "empty", "value"] = "unset"
    text: str = ""
    truncated: bool = False


class ExecStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    where: Literal["build", "target"] = "build"
    kind: Literal["shell", "script"] = "shell"
    argv: list[str] = []
    cwd: str = ""
    env: dict[str, str] = {}
    timeout_ms: int = 0
    result: bool = False
    script_b64: str | None = None
    target_host: str | None = None


class LockSpec(BaseModel):
    path: str


class MarkerSpec(BaseModel):
    path: str


class ExecRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = 1
    run_id: str
    workdir: str
    target_host: str | None = None
    target_key: str | None = None
    target_options: list[str] = []
    lock: LockSpec | None = None
    marker: MarkerSpec | None = None
    clean_paths: list[str] = []
    steps: list[ExecStep] = []


class ExecEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: str
    id: str | None = None
    stream: str | None = None
    chunk: str | None = None
    code: int | None = None
    duration_ms: int | None = None
    tail: str | None = None
    result: ScriptResult | None = None
    pid: int | None = None
    pgid: int | None = None
    message: str | None = None
    cleaned: list[str] | None = None


class ProbeVersion(BaseModel):
    ok: bool = False
    version: str = ""
    hash: str = ""
    os: str = ""
    arch: str = ""
