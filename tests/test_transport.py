from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from ferry.executors import ExecError
from ferry.nodes import NodeSpec
from ferry.transport import (
    artifact_copy_command,
    artifact_transfer_command,
    classify_artifacts_command,
    effective_ignores,
    is_ignored,
    push_stream_to_target,
    safe_extract,
    write_tar,
)


def test_is_ignored_matches_any_component() -> None:
    assert is_ignored("src/build/output.o", ["build"])
    assert is_ignored(".git/config", [".git"])
    assert is_ignored("assets/skip.png", ["*.png"])
    assert not is_ignored("src/main.c", ["build", ".git"])


def test_effective_ignores_handles_single_and_list() -> None:
    assert "build" in effective_ignores([".git"], "./build/out")
    ignores = effective_ignores([".git"], ["./build/out", "dist/app"])
    assert "build" in ignores
    assert "dist" in ignores
    assert ".private.ferry.yaml" in ignores
    assert ".env" in ignores


def test_tar_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.c").write_text("int main;", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "stale.o").write_text("x", encoding="utf-8")

    buffer = io.BytesIO()
    count = write_tar(buffer, root, ["build"])
    assert count == 2

    buffer.seek(0)
    destination = tmp_path / "remote"
    safe_extract(buffer, destination)
    assert (destination / "src" / "main.c").read_text(encoding="utf-8") == "int main;"
    assert not (destination / "build").exists()


def test_safe_extract_rejects_escaping_path(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("../evil.txt")
        payload = b"bad"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    buffer.seek(0)
    with pytest.raises(ExecError):
        safe_extract(buffer, tmp_path / "dest")


def test_artifact_transfer_command_directory() -> None:
    target = NodeSpec(
        key="board",
        destination="board",
        role="deploy",
        user="root",
        port="2222",
        options=("-o", "IdentityFile=/keys/id"),
    )
    command = artifact_transfer_command(
        "/work/demo/build/out", True, target, "/opt/app", key="/home/u/key"
    )
    assert "tar -C /work/demo/build/out -cf - ." in command
    assert "-p 2222" in command
    assert "User=root" in command
    assert "IdentityFile=/keys/id" in command
    assert "-C /opt/app" in command


def test_artifact_transfer_command_file() -> None:
    target = NodeSpec(key="board", destination="board", role="deploy")
    command = artifact_transfer_command(
        "/work/demo/build/a.out", False, target, "/opt/app"
    )
    assert "tar -C /work/demo/build -cf - a.out" in command


def test_artifact_copy_command() -> None:
    directory = artifact_copy_command("/work/out", True, "/opt/app")
    assert "cp -a /work/out/. /opt/app/" in directory
    file_copy = artifact_copy_command("/work/out/a.bin", False, "/opt/app")
    assert "cp -a /work/out/a.bin /opt/app/" in file_copy


def test_classify_artifacts_command() -> None:
    command = classify_artifacts_command("/work/demo", ["./build/out", "dist/a"])
    assert "cd /work/demo" in command
    assert 'if [ -d ./build/out ]' in command
    assert 'if [ -f dist/a ]' in command


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.written = b""
        self.closed = False

    def send(self, data: bytes) -> None:
        self.written += data

    def close_stdin(self) -> None:
        self.closed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        pass


class FakeExecutor:
    def __init__(self) -> None:
        self.process = FakeProcess()

    def check(self, host) -> bool:  # type: ignore[no-untyped-def]
        return True

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        return self.process


def test_push_stream_to_target_writes_chunks() -> None:
    executor = FakeExecutor()
    push_stream_to_target(executor, "board", "/opt/app", [b"abc", b"def"])
    assert executor.process.written == b"abcdef"
    assert executor.process.closed is True
