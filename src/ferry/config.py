from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Where

CONFIG_FILENAME = ".ferry.yaml"

STAGE_ORDER = (
    "pre_sync",
    "sync",
    "post_sync",
    "pre_build",
    "build",
    "post_build",
    "pre_deploy",
    "deploy",
    "post_deploy",
)

SCRIPT_STAGES = (
    "pre_sync",
    "post_sync",
    "pre_build",
    "post_build",
    "pre_deploy",
    "post_deploy",
)

DEFAULT_WHERE: dict[str, Where] = {
    "pre_sync": "dev",
    "post_sync": "build",
    "pre_build": "build",
    "post_build": "build",
    "pre_deploy": "target",
    "post_deploy": "target",
}

DEFAULT_IGNORES = [
    ".git",
    ".ferry",
    ".venv",
    "__pycache__",
    "node_modules",
]


class ConfigError(Exception):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_remote_path(value: str) -> str:
    if not value.startswith("/"):
        raise ValueError("must be an absolute path on the remote node")
    if ".." in PurePosixPath(value).parts:
        raise ValueError("must not contain '..'")
    return value


def _validate_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise ValueError("must be a relative path")
    if ".." in path.parts:
        raise ValueError("must not contain '..'")
    return normalized


class BuildCfg(StrictModel):
    node: str
    workdir: str
    clean: list[str] | None = None
    ssh_key: str | None = None

    @field_validator("node")
    @classmethod
    def _node(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("workdir")
    @classmethod
    def _workdir(cls, value: str) -> str:
        return _validate_remote_path(value)

    @field_validator("ssh_key")
    @classmethod
    def _ssh_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_remote_path(value)

    @field_validator("clean")
    @classmethod
    def _clean(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [_validate_relative_path(item) for item in value]


class DeployCfg(StrictModel):
    node: str
    path: str

    @field_validator("node")
    @classmethod
    def _node(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _validate_remote_path(value)


class ToolchainCfg(StrictModel):
    probe: str | None = None
    expect: str | None = None

    @model_validator(mode="after")
    def _pair(self) -> ToolchainCfg:
        if self.expect is not None and self.probe is None:
            raise ValueError("toolchain.expect requires toolchain.probe")
        return self


class SyncCfg(StrictModel):
    ignore: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORES))


class StageScripts(StrictModel):
    pre_sync: list[str] = Field(default_factory=list)
    post_sync: list[str] = Field(default_factory=list)
    pre_build: list[str] = Field(default_factory=list)
    post_build: list[str] = Field(default_factory=list)
    pre_deploy: list[str] = Field(default_factory=list)
    post_deploy: list[str] = Field(default_factory=list)

    @field_validator("*")
    @classmethod
    def _scripts(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("script entries must be non-empty strings")
        return value

    def for_stage(self, stage: str) -> list[str]:
        if stage not in SCRIPT_STAGES:
            return []
        return list(getattr(self, stage))


class TargetCfg(StrictModel):
    build: BuildCfg
    deploy: DeployCfg
    toolchain: ToolchainCfg = Field(default_factory=ToolchainCfg)
    build_cmd: str
    out: str = "dist"
    stages: StageScripts = Field(default_factory=StageScripts)
    sync: SyncCfg = Field(default_factory=SyncCfg)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("build_cmd")
    @classmethod
    def _build_cmd(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("out")
    @classmethod
    def _out(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("env")
    @classmethod
    def _env(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not key.strip():
                raise ValueError("environment variable names must not be empty")
            if not isinstance(item, str):
                raise ValueError(f"environment variable {key!r} must be a string")
        return value


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
class ResolvedScript:
    spec: str
    path: str
    where: Where


@dataclass(frozen=True)
class LoadedConfig:
    config: FerryConfig
    root: Path
    path: Path


def parse_script_spec(spec: str, stage: str) -> ResolvedScript:
    if stage not in DEFAULT_WHERE:
        raise ConfigError(f"stage {stage!r} does not accept user scripts")
    text = spec.strip()
    where = DEFAULT_WHERE[stage]
    if "@" in text:
        path_text, suffix = text.rsplit("@", 1)
        if suffix not in ("dev", "build", "target"):
            raise ConfigError(
                f"invalid script spec {spec!r}: suffix must be @dev, @build or @target"
            )
        where = suffix  # type: ignore[assignment]
        text = path_text
    if not text:
        raise ConfigError(f"invalid script spec {spec!r}: missing path")
    try:
        normalized = _validate_relative_path(text)
    except ValueError as exc:
        raise ConfigError(f"invalid script spec {spec!r}: {exc}") from exc
    return ResolvedScript(spec=spec, path=normalized, where=where)


def resolve_stage_scripts(target: TargetCfg, stage: str) -> list[ResolvedScript]:
    return [parse_script_spec(spec, stage) for spec in target.stages.for_stage(stage)]


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if (directory / CONFIG_FILENAME).is_file():
            return directory
    raise ConfigError(
        f"no {CONFIG_FILENAME} found from {current}; run 'ferry init' first"
    )


def load_config(start: Path | None = None) -> LoadedConfig:
    root = find_project_root(start)
    path = root / CONFIG_FILENAME
    text = path.read_text(encoding="utf-8")
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    try:
        config = FerryConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"invalid {path}:\n{exc}") from exc
    if not config.project.name:
        config.project.name = root.name
    return LoadedConfig(config=config, root=root, path=path)


def config_to_yaml(config: FerryConfig) -> str:
    data = config.model_dump(mode="json", exclude_none=True)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return "# Ferry project configuration. Commit this file with your project.\n" + body


def diff_config(old: str, new: str, filename: str = CONFIG_FILENAME) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{filename} (current)",
            tofile=f"{filename} (proposed)",
        )
    )


def initial_config(project_name: str) -> FerryConfig:
    return FerryConfig(project=ProjectCfg(name=project_name), targets={})


def add_target(
    config: FerryConfig, name: str, target: TargetCfg, *, replace: bool = False
) -> FerryConfig:
    if name in config.targets and not replace:
        raise ConfigError(
            f"target {name!r} already exists; rerun with --reset to replace it"
        )
    return config.model_copy(update={"targets": {**config.targets, name: target}})


def first_component(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if parts else path


__all__ = [
    "CONFIG_FILENAME",
    "BuildCfg",
    "ConfigError",
    "DeployCfg",
    "FerryConfig",
    "LoadedConfig",
    "ProjectCfg",
    "ResolvedScript",
    "SCRIPT_STAGES",
    "STAGE_ORDER",
    "StageScripts",
    "SyncCfg",
    "TargetCfg",
    "ToolchainCfg",
    "add_target",
    "config_to_yaml",
    "diff_config",
    "find_project_root",
    "first_component",
    "initial_config",
    "load_config",
    "parse_script_spec",
    "resolve_stage_scripts",
]
