from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from ferry.auth import Credentials
from ferry.commands import up as up_command
from ferry.executors import CommandResult


class FakeExecutor:
    def __init__(self, *args, **kwargs) -> None:
        self.calls = []

    def check(self, host) -> bool:  # type: ignore[no-untyped-def]
        return True

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        self.calls.append((host, argv))
        return CommandResult(0, b"", b"")

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def test_up_writes_private_and_strips_nodes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ferry.commands.up.SshExecutor", FakeExecutor)
    monkeypatch.setattr(
        "ferry.commands.up.ensure_remote_probe",
        lambda *args, **kwargs: SimpleNamespace(path="/tmp/probe"),
    )
    monkeypatch.setattr(
        "ferry.commands.up.resolve_credentials", lambda **kwargs: Credentials()
    )

    up_command.up(
        ssh="buildvm",
        build_host=None,
        build_user=None,
        build_port=None,
        target="board",
        target_host=None,
        target_user=None,
        target_port=None,
        name="t1",
        build_workdir="/work/demo",
        out=["./dist"],
        run_build="make",
        run_deploy="echo deployed",
        deploy_workdir="/opt/app",
        transfer_from=None,
        before_build=None,
        after_build=None,
        before_deploy=None,
        after_deploy=None,
        yes=True,
        reset=False,
        ask_pass=False,
    )

    private = tmp_path / ".private.ferry.yaml"
    public = tmp_path / ".ferry.yaml"
    assert private.is_file()
    assert public.is_file()

    private_data = yaml.safe_load(private.read_text(encoding="utf-8"))
    target = private_data["targets"]["t1"]
    assert target["build"]["node"] == "buildvm"
    assert target["deploy"]["node"] == "board"

    public_data = yaml.safe_load(public.read_text(encoding="utf-8"))
    target = public_data["targets"]["t1"]
    assert "node" not in target["build"]
    assert "node" not in target["deploy"]
    assert target["build"]["run_build"] == "make"

    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".private.ferry.yaml" in gitignore
    assert ".env" in gitignore


def test_up_supports_inline_nodes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ferry.commands.up.SshExecutor", FakeExecutor)
    monkeypatch.setattr(
        "ferry.commands.up.ensure_remote_probe",
        lambda *args, **kwargs: SimpleNamespace(path="/tmp/probe"),
    )
    monkeypatch.setattr(
        "ferry.commands.up.resolve_credentials", lambda **kwargs: Credentials()
    )

    up_command.up(
        ssh=None,
        build_host="localhost",
        build_user="root",
        build_port="2222",
        target="dev.node",
        target_host=None,
        target_user=None,
        target_port=None,
        name="local",
        build_workdir="/work/demo",
        out=["./dist"],
        run_build="make",
        run_deploy="true",
        deploy_workdir="/opt/app",
        transfer_from=None,
        before_build=None,
        after_build=None,
        before_deploy=None,
        after_deploy=None,
        yes=True,
        reset=False,
        ask_pass=False,
    )
    private_data = yaml.safe_load(
        (tmp_path / ".private.ferry.yaml").read_text(encoding="utf-8")
    )
    build_node = private_data["targets"]["local"]["build"]["node"]
    assert build_node["hostname"] == "localhost"
    assert build_node["port"] == "2222"


def test_up_local_skips_probe_and_keeps_symbolic_nodes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ferry.commands.up.SshExecutor", FakeExecutor)
    calls: list[object] = []
    monkeypatch.setattr(
        "ferry.commands.up.ensure_remote_probe",
        lambda *args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(
        "ferry.commands.up.resolve_credentials", lambda **kwargs: Credentials()
    )

    up_command.up(
        ssh="dev.node",
        build_host=None,
        build_user=None,
        build_port=None,
        target="dev.node",
        target_host=None,
        target_user=None,
        target_port=None,
        name="local",
        build_workdir=".",
        out=["./dist"],
        run_build="make",
        run_deploy="true",
        deploy_workdir="./deploy",
        transfer_from=None,
        before_build=None,
        after_build=None,
        before_deploy=None,
        after_deploy=None,
        yes=True,
        reset=False,
        ask_pass=False,
    )

    assert calls == []
    public = yaml.safe_load(
        (tmp_path / ".ferry.yaml").read_text(encoding="utf-8")
    )
    target = public["targets"]["local"]
    assert target["build"]["node"] == "dev.node"
    assert target["deploy"]["node"] == "dev.node"
    assert (tmp_path / "deploy").is_dir()
