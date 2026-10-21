from __future__ import annotations

import base64
import threading
from io import BytesIO
from pathlib import Path

import pytest

from ferry.local_probe import LocalProbe
from ferry.models import ExecRequest, ExecStep, MarkerSpec
from ferry.probe import ProbeError
from ferry.transport import safe_extract


def request(
    root: Path,
    script: str,
    *,
    step_id: str = "step",
    marker: Path | None = None,
    clean_paths: list[str] | None = None,
    timeout_ms: int = 0,
) -> ExecRequest:
    return ExecRequest(
        run_id="run-1",
        workdir=str(root),
        marker=MarkerSpec(path=str(marker)) if marker else None,
        clean_paths=clean_paths or [],
        steps=[
            ExecStep(
                id=step_id,
                script_b64=base64.b64encode(script.encode("utf-8")).decode("ascii"),
                cwd=str(root),
                result=True,
                timeout_ms=timeout_ms,
            )
        ],
    )


def test_exec_streams_output_and_result(tmp_path: Path) -> None:
    probe = LocalProbe()
    events = list(
        probe.exec(
            request(
                tmp_path,
                "printf 'hello\\n'; printf 'value\\n' > \"$FERRY_RESULT_FILE\"; "
                "printf 'oops\\n' >&2",
            )
        )
    )
    assert events[0].event == "start"
    assert events[-1].event == "end" and events[-1].code == 0
    output = [
        base64.b64decode(event.chunk).decode()
        for event in events
        if event.event == "out" and event.stream == "stdout"
    ]
    errors = [
        base64.b64decode(event.chunk).decode()
        for event in events
        if event.event == "out" and event.stream == "stderr"
    ]
    assert output == ["hello"]
    assert errors == ["oops"]
    step_end = next(event for event in events if event.event == "step_end")
    assert step_end.result is not None
    assert step_end.result.state == "value"
    assert step_end.result.text == "value"


def test_failure_stops_and_removes_marker(tmp_path: Path) -> None:
    probe = LocalProbe()
    marker = tmp_path / ".ferry" / "t.marker"
    req = request(tmp_path, "echo before; exit 6", marker=marker)
    req.steps.append(
        ExecStep(
            id="second",
            script_b64=base64.b64encode(b"echo never").decode("ascii"),
            cwd=str(tmp_path),
        )
    )
    events = list(probe.exec(req))
    assert events[-1].code == 1
    assert not any(event.id == "second" for event in events)
    assert not marker.exists()


def test_marker_recovery_cleans_outputs(tmp_path: Path) -> None:
    probe = LocalProbe()
    marker = tmp_path / ".ferry" / "t.marker"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "dist"
    artifact.mkdir()
    (artifact / "stale.txt").write_text("stale", encoding="utf-8")
    events = list(
        probe.exec(
            request(
                tmp_path,
                "true",
                marker=marker,
                clean_paths=[str(artifact)],
            )
        )
    )
    recovery = next(event for event in events if event.event == "recovery")
    assert recovery.cleaned == [str(artifact)]
    assert not artifact.exists()
    assert not marker.exists()


def test_terminate_keeps_marker(tmp_path: Path) -> None:
    probe = LocalProbe()
    marker = tmp_path / ".ferry" / "t.marker"
    timer = threading.Timer(0.5, probe.terminate)
    timer.start()
    events = list(probe.exec(request(tmp_path, "sleep 30", marker=marker)))
    timer.cancel()
    assert events[-1].code == 3
    assert marker.exists()


def test_lock_contention(tmp_path: Path) -> None:
    path = str(tmp_path / ".ferry" / "t.lock")
    first = LocalProbe()
    second = LocalProbe()
    assert first.acquire_lock(path) > 0
    with pytest.raises(ProbeError):
        second.acquire_lock(path)
    first.release_lock()
    assert second.acquire_lock(path) > 0
    second.release_lock()


def test_get_stream_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    (source / "nested").mkdir(parents=True)
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / "nested" / "b.txt").write_text("b", encoding="utf-8")
    probe = LocalProbe()
    payload = b"".join(probe.get_stream(str(source)))
    destination = tmp_path / "out"
    safe_extract(BytesIO(payload), destination)
    assert (destination / "a.txt").read_text(encoding="utf-8") == "a"
    assert (destination / "nested" / "b.txt").read_text(encoding="utf-8") == "b"


def test_get_stream_selected_file(tmp_path: Path) -> None:
    (tmp_path / "artifact.bin").write_bytes(b"payload")
    probe = LocalProbe()
    payload = b"".join(
        probe.get_stream(str(tmp_path), ["artifact.bin"])
    )
    destination = tmp_path / "out"
    safe_extract(BytesIO(payload), destination)
    assert (destination / "artifact.bin").read_bytes() == b"payload"


def test_timeout_returns_124(tmp_path: Path) -> None:
    probe = LocalProbe()
    events = list(probe.exec(request(tmp_path, "sleep 30", timeout_ms=300)))
    assert events[-1].code == 1
    step_end = next(event for event in events if event.event == "step_end")
    assert step_end.code == 124
