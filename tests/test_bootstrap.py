from __future__ import annotations

import shutil

import pytest

from ferry.auth import Credentials
from ferry.bootstrap import (
    BootstrapError,
    bootstrap_keys,
    ensure_build_key,
    ensure_local_key,
    install_public_key,
)
from ferry.executors import CommandResult


class FakeExecutor:
    def __init__(self, responses: list[CommandResult]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, list[str], bytes | None]] = []

    def check(self, host: str) -> bool:
        return True

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        self.calls.append((host, argv, stdin))
        if not self.responses:
            return CommandResult(0, b"", b"")
        return self.responses.pop(0)

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def test_install_public_key_is_idempotent_command() -> None:
    executor = FakeExecutor([CommandResult(0, b"", b"")])
    install_public_key(executor, "build", "ssh-ed25519 AAAA ferry")
    command = executor.calls[0][1][-1]
    assert "authorized_keys" in command
    assert "grep -qxF" in command
    assert executor.calls[0][2] == b"ssh-ed25519 AAAA ferry\n"


def test_ensure_build_key_reads_pubkey() -> None:
    executor = FakeExecutor(
        [
            CommandResult(0, b"/home/dev\n", b""),
            CommandResult(0, b"ssh-ed25519 BBBB ferry\n", b""),
        ]
    )
    key_path, public = ensure_build_key(executor, "build")
    assert key_path == "/home/dev/.ferry/keys/id_ed25519"
    assert public == "ssh-ed25519 BBBB ferry"


def test_bootstrap_requires_both_passwords() -> None:
    executor = FakeExecutor([])
    with pytest.raises(BootstrapError):
        bootstrap_keys(
            executor, "build", "target", Credentials(build_password="only-build")
        )


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen required")
def test_ensure_local_key_creates_and_returns_public_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    public = ensure_local_key()
    assert public.startswith("ssh-ed25519 ")
    assert "ferry" in public
    path = tmp_path / "home" / "keys" / "id_ed25519"
    assert path.is_file()
    assert (path.with_name("id_ed25519.pub")).is_file()
