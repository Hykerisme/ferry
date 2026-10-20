from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from ferry.executors import ExecError
from ferry.transport import (
    artifact_push_command,
    effective_ignores,
    is_ignored,
    safe_extract,
    write_tar,
)


def test_is_ignored_matches_any_component() -> None:
    assert is_ignored("src/build/output.o", ["build"])
    assert is_ignored(".git/config", [".git"])
    assert is_ignored("assets/skip.png", ["*.png"])
    assert not is_ignored("src/main.c", ["build", ".git"])


def test_effective_ignores_adds_out_directory() -> None:
    ignores = effective_ignores([".git"], "build/out")
    assert "build" in ignores


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


def test_artifact_push_command_quotes_paths() -> None:
    command = artifact_push_command("/work/proj/build/out", "board", "/opt/my app")
    assert "tar -C /work/proj/build/out -cf - ." in command
    assert "/opt/my app" in command
    assert "-o StrictHostKeyChecking=accept-new board" in command
