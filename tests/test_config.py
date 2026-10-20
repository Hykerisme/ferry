from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ferry.config import (
    ConfigError,
    FerryConfig,
    TargetCfg,
    first_component,
    load_config,
    parse_script_spec,
)

VALID = {
    "version": 1,
    "project": {"name": "demo"},
    "targets": {
        "board": {
            "build": {"node": "buildvm", "workdir": "/work/demo"},
            "deploy": {"node": "board", "path": "/opt/app"},
            "toolchain": {"probe": "cat /opt/toolchain/VERSION", "expect": "v2.4.1"},
            "build_cmd": "cmake -B build && cmake --build build -j",
            "out": "build/out",
            "stages": {
                "post_sync": ["scripts/check-env.sh"],
                "pre_deploy": ["scripts/stop.sh@target"],
                "pre_build": ["scripts/dev-check.sh@dev"],
            },
        }
    },
}


def write_config(root: Path, data: object) -> Path:
    path = root / ".ferry.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    write_config(tmp_path, VALID)
    loaded = load_config(tmp_path)
    target = loaded.config.targets["board"]
    assert target.build.workdir == "/work/demo"
    assert target.toolchain.expect == "v2.4.1"
    assert ".git" in target.sync.ignore


def test_project_name_defaults_to_directory(tmp_path: Path) -> None:
    data = {"targets": {}}
    write_config(tmp_path, data)
    loaded = load_config(tmp_path)
    assert loaded.config.project.name == tmp_path.name


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    data = {"targets": {"b": {**VALID["targets"]["board"], "mystery": 1}}}  # type: ignore[index]
    write_config(tmp_path, data)
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_relative_remote_path_is_rejected(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build"] = {"node": "n", "workdir": "relative/path"}
    write_config(tmp_path, {"targets": {"b": target}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_symlink_escape_in_script_path_is_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_script_spec("../outside.sh", "post_sync")
    with pytest.raises(ConfigError):
        parse_script_spec("/abs/path.sh", "post_sync")


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("pre_sync", "dev"),
        ("post_sync", "build"),
        ("pre_build", "build"),
        ("post_build", "build"),
        ("pre_deploy", "target"),
        ("post_deploy", "target"),
    ],
)
def test_stage_default_locations(stage: str, expected: str) -> None:
    resolved = parse_script_spec("scripts/check.sh", stage)
    assert resolved.where == expected


def test_explicit_location_suffix_overrides_default() -> None:
    resolved = parse_script_spec("scripts/check.sh@dev", "post_deploy")
    assert resolved.path == "scripts/check.sh"
    assert resolved.where == "dev"


def test_invalid_suffix_is_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_script_spec("scripts/check.sh@builder", "post_sync")


def test_config_is_strict_about_stage_names(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["stages"] = {"before_build": ["scripts/x.sh"]}
    write_config(tmp_path, {"targets": {"b": target}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_first_component() -> None:
    assert first_component("build/out") == "build"
    assert first_component("dist") == "dist"


def test_target_requires_build_cmd() -> None:
    with pytest.raises(ValidationError):
        TargetCfg.model_validate(
            {
                "build": {"node": "n", "workdir": "/w"},
                "deploy": {"node": "t", "path": "/d"},
            }
        )


def test_config_dump_roundtrip(tmp_path: Path) -> None:
    from ferry.config import config_to_yaml

    config = FerryConfig.model_validate(VALID)
    text = config_to_yaml(config)
    parsed = yaml.safe_load(text)
    assert parsed["targets"]["board"]["out"] == "build/out"
