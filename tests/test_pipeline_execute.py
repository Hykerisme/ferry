from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ferry.auth import Credentials
from ferry.config import (
    BuildCfg,
    DeployCfg,
    FerryConfig,
    LoadedConfig,
    ProjectCfg,
    TargetCfg,
)
from ferry.executors import CommandResult
from ferry.models import ExecEvent, ExecRequest
from ferry.nodes import NodeSpec, ResolvedTarget
from ferry.pipeline import Pipeline, PipelineContext, PipelineError, render_plan
from ferry.state import load_state
from ferry.topology import Route


def quiet_console() -> Console:
    return Console(file=StringIO())


class FakeProcess:
    def __init__(self, stdout: bytes = b'{"ok":true,"files":1}') -> None:
        self.stdin = BytesIO()
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO()
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
    def __init__(self, events: list) -> None:
        self.events = events
        self.process = FakeProcess()

    def check(self, host) -> bool:  # type: ignore[no-untyped-def]
        return True

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        command = " ".join(argv)
        self.events.append(("run", host.role, command))
        if "id_ed25519" in command:
            return CommandResult(0, b"/home/u/.ferry/keys/id_ed25519", b"")
        if "if [ -d" in command:
            return CommandResult(0, b"dir:./build/out\n", b"")
        if "if [ -f" in command:
            return CommandResult(0, b"Xok=1\n", b"")
        return CommandResult(0, b"", b"")

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        self.events.append(("spawn", host.role, argv))
        return self.process


class FakeProbe:
    def __init__(self, events: list, code: int = 0) -> None:
        self.path = "/tmp/probe"
        self.events = events
        self.code = code
        self.requests: list[ExecRequest] = []

    def exec(self, request: ExecRequest):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        self.events.append(("probe", request))
        yield ExecEvent(event="start", pid=4242, pgid=4242)
        for step in request.steps:
            yield ExecEvent(event="step_start", id=step.id)
            yield ExecEvent(event="step_end", id=step.id, code=self.code)
        yield ExecEvent(event="end", code=self.code)

    def acquire_lock(self, path: str) -> int:
        self.events.append(("lock", path))
        return 5555

    def release_lock(self) -> None:
        self.events.append(("unlock", None))

    def get_stream(self, root: str, paths: list[str] | None = None):  # type: ignore[no-untyped-def]
        self.events.append(("get", root, paths))
        yield b"tar-bytes"

    def terminate(self) -> None:
        pass


def make_target() -> TargetCfg:
    return TargetCfg(
        build=BuildCfg(
            node="build",
            workdir="/work/demo",
            out="./build/out",
            before_build=["scripts/prep.sh"],
            run_build="make -j",
            after_build="echo done",
        ),
        deploy=DeployCfg(
            node="board",
            workdir="/opt/app",
            before_deploy="echo stop",
            run_deploy=["scripts/deploy.sh"],
            after_deploy="echo restarted",
        ),
    )


def make_context(
    tmp_path: Path,
    events: list,
    *,
    deploy_role: str = "external",
    route: Route | None = None,
    probe: FakeProbe | None = None,
    executor: FakeExecutor | None = None,
) -> tuple[Pipeline, PipelineContext, FakeProbe, FakeExecutor]:
    for name in ("prep.sh", "deploy.sh"):
        path = tmp_path / "scripts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target = make_target()
    loaded = LoadedConfig(
        config=FerryConfig(project=ProjectCfg(name="demo"), targets={"board": target}),
        root=tmp_path,
        path=tmp_path / ".ferry.yaml",
    )
    fake_probe = probe or FakeProbe(events)
    fake_executor = executor or FakeExecutor(events)
    deploy = NodeSpec(
        key="board",
        destination="board",
        role="dev" if deploy_role == "dev" else "deploy",
    )
    resolved = ResolvedTarget(
        build=NodeSpec(key="build", destination="build", role="build"),
        deploy=deploy,
        deploy_role=deploy_role,  # type: ignore[arg-type]
        transfer_from=None,
    )
    ctx = PipelineContext(
        loaded=loaded,
        target_name="board",
        target=target,
        resolved=resolved,
        executor=fake_executor,
        probe=fake_probe,  # type: ignore[arg-type]
        env={"FOO": "bar"},
        route=route or Route("build->target", "direct"),
        run_id="run-1",
        log_file=tmp_path / "run.log",
        lock_path="/work/demo/.ferry/board.lock",
        marker_path="/work/demo/.ferry/board.marker",
        clean_paths=["/work/demo/build"],
        build_key="/home/u/.ferry/keys/id_ed25519",
    )
    pipeline = Pipeline(
        loaded,
        "board",
        executor=fake_executor,
        console=quiet_console(),
        credentials=Credentials(),
    )
    return pipeline, ctx, fake_probe, fake_executor


def test_plan_masks_env_values(tmp_path: Path) -> None:
    events: list = []
    pipeline, ctx, _, _ = make_context(tmp_path, events)
    output = StringIO()
    console = Console(file=output, width=200)
    render_plan(console, ctx, pipeline.build_steps())
    text = output.getvalue()
    assert "FOO=***" in text
    assert "bar" not in text


def test_phase_order_and_marker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    events: list = []
    pipeline, ctx, probe, _ = make_context(tmp_path, events)
    steps = pipeline.build_steps()
    pipeline.execute(ctx, steps)

    phases = [event[0] for event in events]
    assert phases.index("probe") < phases.index("spawn")
    assert phases.count("probe") >= 4

    marked = [request for request in probe.requests if request.marker is not None]
    assert len(marked) == 1
    assert all(step.id.startswith(("run_build:", "after_build:")) for step in marked[0].steps)

    target_requests = [request for request in probe.requests if request.target_host]
    assert target_requests
    assert all(request.target_host == "board" for request in target_requests)

    state = load_state("demo", "board")
    assert state is not None
    assert all(stage.state == "ok" for stage in state.stages.values())


def test_failure_marks_stage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    events: list = []
    probe = FakeProbe(events, code=2)
    pipeline, ctx, _, _ = make_context(tmp_path, events, probe=probe)
    with pytest.raises(PipelineError, match="step failed"):
        pipeline.execute(ctx, pipeline.build_steps())
    state = load_state("demo", "board")
    assert state is not None
    assert any(stage.state == "failed" for stage in state.stages.values())


def test_deploy_on_dev_runs_locally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    events: list = []
    executor = FakeExecutor(events)
    monkeypatch.setattr("ferry.pipeline.LocalExecutor", lambda: executor)
    pipeline, ctx, probe, _ = make_context(
        tmp_path,
        events,
        deploy_role="dev",
        route=Route("build->dev", "local", uses_dev_relay=True),
    )
    pipeline.execute(ctx, pipeline.build_steps())
    assert any(event[0] == "get" for event in events)
    assert any(
        event[0] == "run" and "cat > " in event[2]
        for event in events
    )
