from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_FILENAME = ".ferry.yaml"
PRIVATE_CONFIG_FILENAME = ".private.ferry.yaml"

DEFAULT_IGNORES = [
    ".git",
    ".ferry",
    ".private.ferry.yaml",
    ".env",
    ".venv",
    "__pycache__",
    "node_modules",
]

# A single string is a shell command; a list is a list of script paths.
CommandSpec = str | list[str]
NodeConfig = str | dict[str, Any] | None


class ConfigError(Exception):
    pass


class NodeConfigMissing(ConfigError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _is_absolute_path(value: str) -> bool:
    text = value.strip()
    return text.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", text))


def _validate_workdir(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty")
    if not _is_absolute_path(value):
        raise ValueError("must be an absolute path")
    return value


def _validate_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or re.match(r"^[A-Za-z]:/", normalized):
        raise ValueError("must be a relative path")
    if ".." in path.parts:
        raise ValueError("must not contain '..'")
    if not normalized or normalized == ".":
        raise ValueError("must not be empty")
    return normalized


def _apply_command_validator(value: CommandSpec | None, *, required: bool) -> CommandSpec | None:
    if value is None:
        if required:
            raise ValueError("this field is required")
        return None
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("shell command must not be empty")
        return value
    if not isinstance(value, list):
        raise ValueError("must be a shell command string or a list of script paths")
    if required and not value:
        raise ValueError("must contain at least one command or script")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("script paths must be non-empty strings")
        _validate_relative_path(item)
    return [_validate_relative_path(item) for item in value]


class InlineNode(StrictModel):
    model_config = ConfigDict(extra="allow")

    hostname: str | None = None
    user: str | None = None
    port: int | str | None = None

    @model_validator(mode="after")
    def _validate_host_alias(self) -> InlineNode:
        extras = dict(self.model_extra or {})
        hostname = (
            self.hostname
            or extras.get("HostName")
            or extras.get("hostName")
            or extras.get("hostname")
        )
        if not isinstance(hostname, str) or not hostname.strip():
            raise ValueError("inline node requires a non-empty hostname")
        for key, item in extras.items():
            if key.lower() in {"host", "match", "include"}:
                raise ValueError(f"{key!r} is not allowed in an inline node")
            if not isinstance(item, (str, int, float, bool)):
                raise ValueError(
                    f"inline node option {key!r} must be a scalar value"
                )
        return self

    def resolved_name(self) -> str:
        extras = self.model_extra or {}
        hostname = (
            self.hostname
            or extras.get("HostName")
            or extras.get("hostName")
            or extras.get("hostname")
        )
        return str(hostname)


class SyncCfg(StrictModel):
    ignore: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORES))


class BuildCfg(StrictModel):
    node: str | InlineNode | None = None
    workdir: str
    out: str | list[str]
    before_build: CommandSpec | None = None
    run_build: CommandSpec
    after_build: CommandSpec | None = None

    @field_validator("node")
    @classmethod
    def _node(cls, value: str | InlineNode | None) -> str | InlineNode | None:
        if isinstance(value, str) and value in {"dev.node", "build.node"}:
            raise ValueError("build.node must be a concrete alias or inline host alias")
        return value

    @field_validator("workdir")
    @classmethod
    def _workdir(cls, value: str) -> str:
        normalized = _validate_workdir(value)
        if not normalized.startswith("/"):
            raise ValueError("build.workdir must be an absolute POSIX path")
        return normalized

    @field_validator("out")
    @classmethod
    def _out(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            return _validate_relative_path(value)
        if not value:
            raise ValueError("out must contain at least one path")
        return [_validate_relative_path(item) for item in value]

    @field_validator("before_build", "run_build", "after_build", mode="before")
    @classmethod
    def _commands(cls, value: CommandSpec | None, info: Any) -> CommandSpec | None:
        return _apply_command_validator(value, required=info.field_name == "run_build")


class DeployCfg(StrictModel):
    node: str | InlineNode | None = None
    transfer_from: Literal["dev.node", "build.node"] | None = None
    workdir: str
    before_deploy: CommandSpec | None = None
    run_deploy: CommandSpec
    after_deploy: CommandSpec | None = None

    @field_validator("workdir")
    @classmethod
    def _workdir(cls, value: str) -> str:
        return _validate_workdir(value)

    @field_validator("before_deploy", "run_deploy", "after_deploy", mode="before")
    @classmethod
    def _commands(cls, value: CommandSpec | None, info: Any) -> CommandSpec | None:
        return _apply_command_validator(value, required=info.field_name == "run_deploy")


class TargetCfg(StrictModel):
    sync: SyncCfg = Field(default_factory=SyncCfg)
    build: BuildCfg
    deploy: DeployCfg


class ProjectCfg(StrictModel):
    name: str | None = None


class FerryConfig(StrictModel):
    version: int = 1
    project: ProjectCfg = Field(default_factory=ProjectCfg)
    targets: dict[str, TargetCfg] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"unsupported config version: {value}")
        return value


@dataclass(frozen=True)
class LoadedConfig:
    config: FerryConfig
    root: Path
    path: Path
    private_path: Path | None = None


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if (directory / CONFIG_FILENAME).is_file() or (
            directory / PRIVATE_CONFIG_FILENAME
        ).is_file():
            return directory
    raise ConfigError(
        f"no {CONFIG_FILENAME} found from {current}; run 'ferry init' first"
    )


def load_config(start: Path | None = None) -> LoadedConfig:
    root = find_project_root(start)
    private = root / PRIVATE_CONFIG_FILENAME
    public = root / CONFIG_FILENAME
    source = private if private.is_file() else public
    if not source.is_file():
        raise ConfigError(
            f"no {CONFIG_FILENAME} found in {root}; run 'ferry init' first"
        )
    text = source.read_text(encoding="utf-8")
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {source}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{source} must contain a YAML mapping")
    try:
        config = FerryConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"invalid {source}:\n{exc}") from exc
    if not config.project.name:
        config.project.name = root.name
    return LoadedConfig(
        config=config,
        root=root,
        path=source,
        private_path=private if private.is_file() else None,
    )


def require_nodes(config: FerryConfig) -> None:
    missing: list[str] = []
    for name, target in config.targets.items():
        if target.build.node is None:
            missing.append(f"{name}.build.node")
        if target.deploy.node is None:
            missing.append(f"{name}.deploy.node")
    if missing:
        raise NodeConfigMissing(
            "missing node definitions: "
            + ", ".join(missing)
            + ". Run 'ferry up' to fill them in, or create .private.ferry.yaml"
            " with the real node settings."
        )


def config_to_yaml(config: FerryConfig) -> str:
    data = config.model_dump(mode="json", exclude_none=True)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return "# Ferry project configuration. Commit this file with your project.\n" + body


def initial_config(project_name: str) -> FerryConfig:
    return FerryConfig(project=ProjectCfg(name=project_name), targets={})


def diff_config(old: str, new: str, filename: str = CONFIG_FILENAME) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{filename} (current)",
            tofile=f"{filename} (proposed)",
        )
    )
