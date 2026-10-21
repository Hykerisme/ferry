from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ferry.config import (
    ConfigError,
    FerryConfig,
    InlineNode,
    NodeConfigMissing,
    load_config,
    require_nodes,
)

VALID = {
    "version": 1,
    "project": {"name": "demo"},
    "targets": {
        "board": {
            "sync": {"ignore": [".git", ".private.ferry.yaml", ".env"]},
            "build": {
                "node": "buildvm",
                "workdir": "/work/demo",
                "out": "./build/out",
                "before_build": "echo prepare",
                "run_build": "make -j",
                "after_build": ["scripts/after-build.sh"],
            },
            "deploy": {
                "node": "board",
                "transfer_from": "build.node",
                "workdir": "/opt/app",
                "before_deploy": ["scripts/before-deploy.sh"],
                "run_deploy": "echo deploy",
                "after_deploy": "echo done",
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
    assert target.build.out == "./build/out"
    assert target.build.run_build == "make -j"
    assert target.deploy.transfer_from == "build.node"
    assert target.sync.ignore[0] == ".git"


def test_project_name_defaults_to_directory(tmp_path: Path) -> None:
    write_config(tmp_path, {"targets": {}})
    loaded = load_config(tmp_path)
    assert loaded.config.project.name == tmp_path.name


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    data = {"targets": {"b": {**VALID["targets"]["board"], "mystery": 1}}}  # type: ignore[index]
    write_config(tmp_path, data)
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_legacy_fields_are_rejected(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build_cmd"] = "make"
    write_config(tmp_path, {"targets": {"b": target}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_relative_workdir_is_rejected(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build"] = {"node": "n", "workdir": "relative", "out": "./dist", "run_build": "true"}
    write_config(tmp_path, {"targets": {"b": target}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_windows_workdir_is_accepted(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["deploy"] = {
        "node": "dev.node",
        "workdir": r"C:\deploy\app",
        "run_deploy": "true",
    }
    write_config(tmp_path, {"targets": {"b": target}})
    loaded = load_config(tmp_path)
    assert loaded.config.targets["b"].deploy.workdir == "C:/deploy/app"


def test_build_node_cannot_be_a_reference(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build"] = {
        "node": "build.node",
        "workdir": "/work",
        "out": "./dist",
        "run_build": "true",
    }
    write_config(tmp_path, {"targets": {"b": target}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_deploy_node_accepts_references(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["deploy"] = {"node": "dev.node", "workdir": "/opt/app", "run_deploy": "true"}
    write_config(tmp_path, {"targets": {"b": target}})
    loaded = load_config(tmp_path)
    assert loaded.config.targets["b"].deploy.node == "dev.node"


def test_local_build_allows_relative_workdir(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build"] = {
        "node": "dev.node",
        "workdir": ".",
        "out": "./dist",
        "run_build": "true",
    }
    target["deploy"] = {
        "node": "dev.node",
        "workdir": "./deploy",
        "run_deploy": "true",
    }
    write_config(tmp_path, {"targets": {"b": target}})
    loaded = load_config(tmp_path)
    assert loaded.config.targets["b"].build.workdir == "."
    assert loaded.config.targets["b"].deploy.workdir == "./deploy"


def test_local_workdir_rejects_parent_traversal(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build"] = {
        "node": "dev.node",
        "workdir": "../outside",
        "out": "./dist",
        "run_build": "true",
    }
    write_config(tmp_path, {"targets": {"b": target}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_inline_node_accepts_ssh_host_keys(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build"] = {
        "node": {
            "hostname": "localhost",
            "port": "2222",
            "user": "root",
            "IdentityFile": "/home/dev/.ssh/id_ed25519",
        },
        "workdir": "/work",
        "out": "./dist",
        "run_build": "true",
    }
    write_config(tmp_path, {"targets": {"b": target}})
    loaded = load_config(tmp_path)
    node = loaded.config.targets["b"].build.node
    assert isinstance(node, InlineNode)
    assert node.resolved_name() == "localhost"


def test_inline_node_requires_hostname() -> None:
    with pytest.raises(ValidationError):
        InlineNode.model_validate({"user": "root"})


def test_inline_node_rejects_reserved_keys() -> None:
    with pytest.raises(ValidationError):
        InlineNode.model_validate({"hostname": "x", "Host": "y"})


def test_run_build_and_run_deploy_are_required(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build"] = {"node": "n", "workdir": "/work", "out": "./dist"}
    write_config(tmp_path, {"targets": {"b": target}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_script_arrays_reject_escaping_paths(tmp_path: Path) -> None:
    target = dict(VALID["targets"]["board"])  # type: ignore[index]
    target["build"] = {
        "node": "n",
        "workdir": "/work",
        "out": "./dist",
        "run_build": ["../outside.sh"],
    }
    write_config(tmp_path, {"targets": {"b": target}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_private_config_takes_precedence(tmp_path: Path) -> None:
    write_config(tmp_path, {"targets": {}})
    private = tmp_path / ".private.ferry.yaml"
    private.write_text(yaml.safe_dump(VALID, sort_keys=False), encoding="utf-8")
    loaded = load_config(tmp_path)
    assert loaded.path == private
    assert loaded.private_path == private
    assert "board" in loaded.config.targets


def test_missing_nodes_are_detected(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        {
            "targets": {
                "b": {
                    "build": {
                        "workdir": "/work",
                        "out": "./dist",
                        "run_build": "true",
                    },
                    "deploy": {
                        "workdir": "/opt",
                        "run_deploy": "true",
                    },
                }
            }
        },
    )
    loaded = load_config(tmp_path)
    with pytest.raises(NodeConfigMissing, match="build.node"):
        require_nodes(loaded.config)


def test_config_dump_roundtrip() -> None:
    config = FerryConfig.model_validate(VALID)
    assert config.targets["board"].build.out == "./build/out"
