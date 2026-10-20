from __future__ import annotations

import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

SSH_OPTS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=3",
]


class ExecError(Exception):
    pass


class CommandTimeout(ExecError):
    pass


@dataclass
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


class Process:
    def __init__(self, popen: subprocess.Popen[bytes], description: str) -> None:
        self._popen = popen
        self.description = description

    @property
    def stdin(self) -> IO[bytes] | None:
        return self._popen.stdin

    @property
    def stdout(self) -> IO[bytes] | None:
        return self._popen.stdout

    @property
    def stderr(self) -> IO[bytes] | None:
        return self._popen.stderr

    def send(self, data: bytes) -> None:
        if self._popen.stdin is None:
            raise ExecError("process stdin is not available")
        self._popen.stdin.write(data)

    def close_stdin(self) -> None:
        if self._popen.stdin is not None:
            self._popen.stdin.close()

    def wait(self, timeout: float | None = None) -> int:
        try:
            return self._popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self.terminate()
            raise CommandTimeout(f"{self.description} timed out") from exc

    def terminate(self) -> None:
        if self._popen.poll() is None:
            self._popen.terminate()

    def kill(self) -> None:
        if self._popen.poll() is None:
            self._popen.kill()


class Executor(Protocol):
    def check(self, host: str) -> bool: ...

    def run(
        self,
        host: str,
        argv: list[str],
        stdin: bytes | None = None,
        timeout: float | None = None,
    ) -> CommandResult: ...

    def spawn(
        self,
        host: str,
        argv: list[str],
        stdin: bytes | None = None,
    ) -> Process: ...


def explain_ssh_failure(host: str, stderr: str) -> str:
    lowered = stderr.lower()
    if "remote host identification has changed" in lowered:
        return (
            f"host key for {host} changed (node was probably re-created). "
            f"Review it with 'ssh-keygen -R {host}' and then rerun 'ferry up'."
        )
    if "permission denied" in lowered or "no supported authentication methods" in lowered:
        return f"SSH authentication to {host} failed; check ~/.ssh/config and keys"
    if "connection timed out" in lowered or "connection refused" in lowered:
        return f"cannot reach {host} over SSH"
    return f"SSH command on {host} failed: {stderr.strip() or 'unknown error'}"


class SshExecutor:
    def __init__(
        self,
        *,
        accept_new_host_keys: bool = False,
        connect_timeout: int = 5,
    ) -> None:
        self.accept_new_host_keys = accept_new_host_keys
        self.connect_timeout = connect_timeout

    def options(self) -> list[str]:
        opts = list(SSH_OPTS)
        opts[opts.index("ConnectTimeout=5")] = f"ConnectTimeout={self.connect_timeout}"
        policy = "accept-new" if self.accept_new_host_keys else "yes"
        opts.extend(["-o", f"StrictHostKeyChecking={policy}"])
        return opts

    def _command(self, host: str, argv: list[str]) -> list[str]:
        if not argv:
            raise ExecError("empty remote command")
        return ["ssh", *self.options(), host, shlex.join(argv)]

    def check(self, host: str) -> bool:
        try:
            result = self.run(host, ["true"], timeout=self.connect_timeout + 2)
        except (ExecError, OSError):
            return False
        return result.returncode == 0

    def run(
        self,
        host: str,
        argv: list[str],
        stdin: bytes | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                self._command(host, argv),
                input=stdin,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeout(f"command on {host} timed out: {argv[0]}") from exc
        except OSError as exc:
            raise ExecError(f"failed to invoke ssh: {exc}") from exc
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def spawn(
        self,
        host: str,
        argv: list[str],
        stdin: bytes | None = None,
    ) -> Process:
        command = self._command(host, argv)
        popen = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        process = Process(popen, f"{argv[0]} on {host}")
        if stdin is not None:
            process.send(stdin)
            process.close_stdin()
        return process


class LocalExecutor:
    def __init__(self, shell: str | None = None) -> None:
        self.shell = shell

    def check(self, host: str) -> bool:
        return True

    def run(
        self,
        host: str,
        argv: list[str],
        stdin: bytes | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeout(f"local command timed out: {argv[0]}") from exc
        except OSError as exc:
            raise ExecError(f"failed to run local command: {exc}") from exc
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def spawn(
        self,
        host: str,
        argv: list[str],
        stdin: bytes | None = None,
    ) -> Process:
        popen = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        process = Process(popen, f"local {argv[0]}")
        if stdin is not None:
            process.send(stdin)
            process.close_stdin()
        return process


def remote_mkdir_and_upload(
    executor: Executor,
    host: str,
    remote_path: str,
    data: bytes,
) -> None:
    parent = str(Path(remote_path).parent).replace("\\", "/")
    quoted_parent = shlex.quote(parent)
    quoted_path = shlex.quote(remote_path)
    script = f"mkdir -p {quoted_parent} && cat > {quoted_path} && chmod +x {quoted_path}"
    result = executor.run(host, ["sh", "-c", script], stdin=data, timeout=120)
    if result.returncode != 0:
        raise ExecError(
            f"failed to upload {remote_path} to {host}: {result.stderr_text.strip()}"
        )


def quote_remote(value: str) -> str:
    return shlex.quote(value)


def shell_join(argv: list[str]) -> str:
    return shlex.join(argv)


def current_python() -> str:
    return sys.executable


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
