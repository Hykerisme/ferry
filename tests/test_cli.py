from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from ferry.cli import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip().startswith("ferry ")


def test_init_creates_config_and_gitignore(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    config = yaml.safe_load((tmp_path / ".ferry.yaml").read_text(encoding="utf-8"))
    assert config["targets"] == {}
    assert config["project"]["name"] == tmp_path.name
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".private.ferry.yaml" in gitignore


def test_init_refuses_to_overwrite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_status_without_config_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "ferry init" in result.output


def test_plan_without_nodes_is_interrupted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = {
        "version": 1,
        "project": {"name": "demo"},
        "targets": {
            "board": {
                "build": {
                    "workdir": "/work/demo",
                    "out": "./dist",
                    "run_build": "make",
                },
                "deploy": {
                    "workdir": "/opt/app",
                    "run_deploy": "true",
                },
            }
        },
    }
    (tmp_path / ".ferry.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    result = runner.invoke(app, ["plan", "board"])
    assert result.exit_code == 1
    assert "missing build.node" in result.output
