from __future__ import annotations

from io import BytesIO

import pytest

from ferry.executors import CommandResult, ExecError, SshExecutor
from ferry.transport import push_stream_to_target, run_target_script


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = BytesIO()
        self.stdout = BytesIO()
        self.stderr = BytesIO()
        self.written = b""
        self.closed = False
        self.terminated = False

    def send(self, data: bytes) -> None:
        self.written += data

    def close_stdin(self) -> None:
        self.closed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        self.terminated = True


class RecordingExecutor:
    def __init__(self, result_stdout: bytes = b"Xok=1\n") -> None:
        self.calls: list[tuple[str, list[str], bytes | None]] = []
        self.result_stdout = result_stdout
        self.process = FakeProcess()

    def check(self, host: str) -> bool:
        return True

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        self.calls.append((host, argv, stdin))
        command = " ".join(argv)
        if "if [ -f" in command:
            return CommandResult(0, self.result_stdout, b"")
        return CommandResult(0, b"script output\n", b"")

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        return self.process


def test_password_options_and_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        "ferry.executors.shutil.which", lambda name: "/usr/bin/ferry-askpass"
    )
    executor = SshExecutor(
        passwords={"build": "secret"},
        identity="/home/dev/.ferry/keys/id_ed25519",
    )
    options = executor.options("build")
    assert "BatchMode=yes" not in options
    assert "PreferredAuthentications=password,keyboard-interactive" in options
    assert "PubkeyAuthentication=no" in options
    assert "-i" in options
    env = executor.environment("build")
    assert env is not None
    assert env["SSH_ASKPASS"] == "/usr/bin/ferry-askpass"
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    assert env["FERRY_ASKPASS_SECRET"] == "secret"


def test_key_options_use_batch_mode() -> None:
    executor = SshExecutor()
    options = executor.options("build")
    assert "BatchMode=yes" in options
    assert "PreferredAuthentications=password,keyboard-interactive" not in options
    assert executor.environment("build") is None


def test_missing_askpass_program(monkeypatch) -> None:
    monkeypatch.setattr("ferry.executors.shutil.which", lambda name: None)
    executor = SshExecutor(default_password="secret")
    with pytest.raises(ExecError, match="ferry-askpass"):
        executor.environment("anything")


def test_run_target_script_reports_result() -> None:
    executor = RecordingExecutor()
    outcome = run_target_script(
        executor,
        "board",
        b"#!/bin/sh\nexit 0\n",
        "/opt/app",
        {"XB_TARGET_ARCH": "arm64"},
    )
    assert outcome.code == 0
    assert outcome.result == "ok=1"
    assert outcome.stdout == "script output\n"
    command = executor.calls[0][1][-1]
    assert "FERRY_RESULT_FILE=" in command
    assert executor.calls[0][2] == b"#!/bin/sh\nexit 0\n"


def test_run_target_script_empty_result() -> None:
    executor = RecordingExecutor(result_stdout=b"X")
    outcome = run_target_script(executor, "board", b"exit 0\n", "/opt", {})
    assert outcome.result == "(empty)"


def test_push_stream_to_target_writes_chunks() -> None:
    executor = RecordingExecutor()
    push_stream_to_target(executor, "board", "/opt/app", [b"abc", b"def"])
    assert executor.process.written == b"abcdef"
    assert executor.process.closed is True
