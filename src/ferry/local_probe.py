from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any, BinaryIO

from .models import ExecEvent, ExecRequest, ExecStep, ScriptResult
from .probe import ProbeError
from .transport import write_tar_paths

_msvcrt: Any = None
_fcntl: Any = None
if os.name == "nt":  # pragma: no cover - platform branch
    import msvcrt

    _msvcrt = msvcrt
else:  # pragma: no cover - platform branch
    import fcntl

    _fcntl = fcntl


def _killpg(pid: int, sig: int) -> None:
    os.killpg(pid, sig)  # type: ignore[attr-defined]


def _getpgid(pid: int) -> int:
    return int(os.getpgid(pid))  # type: ignore[attr-defined]


def _getpgrp() -> int:
    return int(getattr(os, "getpgrp", os.getpid)())


_sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)


class LocalProbe:
    """In-process replacement for the Go probe when build runs on dev."""

    path = ""

    def __init__(self) -> None:
        self._lock_file: BinaryIO | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._interrupted = False
        self._timed_out = False

    # ---------------- lock ----------------

    def acquire_lock(self, path: str) -> int:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = target.open("a+b")
        try:
            if os.name == "nt":
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
            else:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ProbeError(
                "another ferry run is active for this target"
            ) from exc
        self._lock_file = handle
        return os.getpid()

    def release_lock(self) -> None:
        handle = self._lock_file
        self._lock_file = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                handle.seek(0)
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
            else:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        finally:
            handle.close()

    # ---------------- marker ----------------

    def _write_marker(self, path: str, run_id: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "run_id": run_id,
                "pid": os.getpid(),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)

    @staticmethod
    def _remove_path(path: str) -> None:
        target = Path(path)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            try:
                target.unlink()
            except OSError:
                pass

    # ---------------- process control ----------------

    def terminate(self) -> None:
        self._interrupted = True
        process = self._process
        if process is not None and process.poll() is None:
            self._terminate_process(process)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":  # pragma: no cover - platform branch
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
            )
            return
        try:
            _killpg(_getpgid(process.pid), signal.SIGTERM)
        except OSError:
            process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                _killpg(_getpgid(process.pid), _sigkill)
            except OSError:
                process.kill()

    # ---------------- execution ----------------

    def exec(self, request: ExecRequest) -> Iterator[ExecEvent]:
        self._interrupted = False
        self._timed_out = False
        abnormal = False
        pid = os.getpid()
        pgid = pid if os.name == "nt" else _getpgrp()
        yield ExecEvent(event="start", pid=pid, pgid=pgid)

        marker_path = request.marker.path if request.marker else None
        if marker_path and Path(marker_path).exists():
            cleaned: list[str] = []
            for path in request.clean_paths:
                if not path:
                    continue
                if Path(path).exists():
                    self._remove_path(path)
                cleaned.append(path)
            yield ExecEvent(event="recovery", cleaned=cleaned)
        if marker_path:
            self._write_marker(marker_path, request.run_id)

        code = 0
        try:
            for step in request.steps:
                if self._interrupted:
                    code = 3
                    break
                yield ExecEvent(event="step_start", id=step.id)
                started = time.monotonic()
                outcome = yield from self._run_step(request, step)
                yield ExecEvent(
                    event="step_end",
                    id=step.id,
                    code=outcome[0],
                    duration_ms=int((time.monotonic() - started) * 1000),
                    tail=outcome[1],
                    result=outcome[2],
                )
                if self._interrupted:
                    code = 3
                    break
                if outcome[0] != 0:
                    code = 1
                    break
        except (KeyboardInterrupt, GeneratorExit):
            abnormal = True
            process = self._process
            if process is not None:
                self._terminate_process(process)
            raise
        finally:
            if marker_path and not abnormal and not self._interrupted:
                try:
                    Path(marker_path).unlink()
                except OSError:
                    pass
        if self._interrupted:
            code = 3
        yield ExecEvent(event="end", code=code)

    def _run_step(
        self, request: ExecRequest, step: ExecStep
    ) -> Generator[ExecEvent, None, tuple[int, str, ScriptResult | None]]:
        script_b64 = step.script_b64
        argv = step.argv
        cwd = step.cwd or request.workdir
        env = dict(os.environ)
        env.update(step.env)
        result_file: str | None = None
        script_file: str | None = None
        if step.result:
            handle, result_file = tempfile.mkstemp(prefix="ferry-result-")
            os.close(handle)
            env["FERRY_RESULT_FILE"] = result_file

        if script_b64:
            content = base64.b64decode(script_b64)
            handle, script_file = tempfile.mkstemp(prefix="ferry-script-", suffix=".sh")
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
            command = ["sh", script_file.replace("\\", "/")]
        elif argv:
            command = list(argv)
        else:
            return (2, "empty step", None)

        tail: list[str] = []
        queue_: queue.Queue[tuple[str, str | None]] = queue.Queue()
        process: subprocess.Popen[bytes] | None = None
        return_value: tuple[int, str, ScriptResult | None] = (2, "empty step", None)
        try:
            popen_args: dict[str, Any] = {}
            if os.name != "nt":
                popen_args["start_new_session"] = True
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **popen_args,
                )
            except OSError as exc:
                return (127, f"cannot start step: {exc}", None)
            self._process = process

            def pump(pipe, stream: str) -> None:  # type: ignore[no-untyped-def]
                assert pipe is not None
                try:
                    for line in iter(pipe.readline, b""):
                        text = line.rstrip(b"\r\n").decode("utf-8", "replace")
                        queue_.put((stream, text))
                except (ValueError, OSError):
                    pass
                finally:
                    queue_.put((stream, None))

            threads = [
                threading.Thread(target=pump, args=(process.stdout, "stdout"), daemon=True),
                threading.Thread(target=pump, args=(process.stderr, "stderr"), daemon=True),
            ]
            for thread in threads:
                thread.start()
            timer = None
            if step.timeout_ms > 0:
                def on_timeout() -> None:
                    self._timed_out = True
                    if process is not None:
                        self._terminate_process(process)

                timer = threading.Timer(step.timeout_ms / 1000, on_timeout)
                timer.daemon = True
                timer.start()

            open_streams = 2
            drain_deadline: float | None = None
            while open_streams:
                try:
                    stream_name, line = queue_.get(timeout=0.2)
                except queue.Empty:
                    # Orphaned children (common under Git for Windows) can keep
                    # inherited pipes open after the main process exits. Stop
                    # waiting once the process is gone; pump threads are daemons.
                    if process.poll() is not None:
                        now = time.monotonic()
                        if drain_deadline is None:
                            drain_deadline = now + 1.0
                        elif now >= drain_deadline:
                            break
                    continue
                drain_deadline = None
                if line is None:
                    open_streams -= 1
                    continue
                tail.append(line)
                if len(tail) > 20:
                    tail.pop(0)
                yield ExecEvent(
                    event="out",
                    id=step.id,
                    stream=stream_name,
                    chunk=base64.b64encode(line.encode("utf-8")).decode("ascii"),
                )

            process.wait()
            if timer is not None:
                timer.cancel()
            self._process = None
            code = process.returncode or 0
            if self._timed_out:
                code = 124
            result = _read_result(result_file) if result_file is not None else None
            return_value = (code, "\n".join(tail), result)
        except BaseException:
            if process is not None and process.poll() is None:
                self._terminate_process(process)
            raise
        finally:
            if script_file:
                try:
                    os.unlink(script_file)
                except OSError:
                    pass
            if result_file:
                try:
                    os.unlink(result_file)
                except OSError:
                    pass
            self._process = None
        return return_value

    # ---------------- artifact stream ----------------

    def get_stream(self, root: str, paths: list[str] | None = None) -> Iterator[bytes]:
        read_fd, write_fd = os.pipe()
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                with os.fdopen(write_fd, "wb") as stream:
                    write_tar_paths(stream, Path(root), paths)
            except BaseException as exc:  # pragma: no cover - defensive
                errors.append(exc)
                try:
                    os.close(write_fd)
                except OSError:
                    pass

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        with os.fdopen(read_fd, "rb") as stream:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                yield chunk
        thread.join()
        if errors:
            raise ProbeError(f"local tar failed: {errors[0]}")


def _read_result(path: str) -> ScriptResult:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ScriptResult(state="unset")
    if not data:
        return ScriptResult(state="empty")
    text = data.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    truncated = len(text) > 512
    if truncated:
        text = text[:512]
    return ScriptResult(state="value", text=text, truncated=truncated)
