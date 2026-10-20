from __future__ import annotations

import shutil
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ferry.config import (
    BuildCfg,
    DeployCfg,
    FerryConfig,
    LoadedConfig,
    ProjectCfg,
    StageScripts,
    TargetCfg,
)
from ferry.models import ExecEvent, ExecRequest, NodeInfo
from ferry.pipeline import Pipeline, PipelineContext, PipelineError
from ferry.state import load_state
from ferry.topology import Route

requires_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")


def quiet_console() -> Console:
    return Console(file=StringIO())


class FakeProbe:
    def __init__(self) -> None:
        self.path = "/tmp/ferry-probe"
        self.requests: list[ExecRequest] = []
        self.lock_path: str | None = None
        self.lock_released = False

    def exec(self, request: ExecRequest):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        yield ExecEvent(event="start", pid=4242, pgid=4242)
        for step in request.steps:
            yield ExecEvent(event="step_start", id=step.id)
            yield ExecEvent(event="step_end", id=step.id, code=0, duration_ms=10)
        yield ExecEvent(event="end", code=0)

    def terminate(self) -> None:
        pass

    def acquire_lock(self, lock_path: str) -> int:
        self.lock_path = lock_path
        return 5555

    def release_lock(self) -> None:
        self.lock_released = True


class FakeExecutor:
    def check(self, host: str) -> bool:
        return True

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def make_context(tmp_path: Path) -> tuple[PipelineContext, FakeProbe]:
    for name in ("pre-sync", "post-sync", "pre-build", "post-build", "pre-deploy", "post-deploy"):
        script = tmp_path / "scripts" / f"{name}.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target = TargetCfg(
        build=BuildCfg(node="build", workdir="/work/demo"),
        deploy=DeployCfg(node="board", path="/opt/app"),
        build_cmd="make -j",
        out="build/out",
        stages=StageScripts(
            pre_sync=["scripts/pre-sync.sh@dev"],
            post_sync=["scripts/post-sync.sh"],
            pre_build=["scripts/pre-build.sh"],
            post_build=["scripts/post-build.sh"],
            pre_deploy=["scripts/pre-deploy.sh"],
            post_deploy=["scripts/post-deploy.sh"],
        ),
    )
    loaded = LoadedConfig(
        config=FerryConfig(project=ProjectCfg(name="demo"), targets={"board": target}),
        root=tmp_path,
        path=tmp_path / ".ferry.yaml",
    )
    probe = FakeProbe()
    ctx = PipelineContext(
        loaded=loaded,
        target_name="board",
        target=target,
        executor=FakeExecutor(),
        probe=probe,  # type: ignore[arg-type]
        info=NodeInfo(os="linux", arch="arm64"),
        env={"XB_TARGET_ARCH": "arm64"},
        route=Route(name="build->target", description="direct"),
        run_id="run-1",
        log_file=tmp_path / "run.log",
        lock_path="/work/demo/.ferry/board.lock",
        marker_path="/work/demo/.ferry/board.marker",
        clean_paths=["/work/demo/build"],
    )
    return ctx, probe


@requires_sh
def test_execute_groups_stages_and_sets_lock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    ctx, probe = make_context(tmp_path)
    pipeline = Pipeline(
        ctx.loaded, "board", executor=FakeExecutor(), console=quiet_console()
    )
    steps = [step for step in pipeline.build_steps() if step.kind != "sync"]
    pipeline.execute(ctx, steps)

    build_requests = [request for request in probe.requests if request.marker is not None]
    assert probe.lock_path == "/work/demo/.ferry/board.lock"
    assert probe.lock_released is True
    assert len(build_requests) == 1
    build_request = build_requests[0]
    assert len(build_request.steps) == 3
    assert all(step.script_b64 is None for step in build_request.steps)
    assert build_request.clean_paths == ["/work/demo/build"]

    target_requests = [
        request
        for request in probe.requests
        if any(step.where == "target" for step in request.steps)
    ]
    assert len(target_requests) == 2
    assert len(target_requests[0].steps) == 1
    assert len(target_requests[1].steps) == 1

    deploy_requests = [
        request
        for request in probe.requests
        if any(step.where == "build" and "tar -C" in " ".join(step.argv) for step in request.steps)
    ]
    assert len(deploy_requests) == 1

    state = load_state("demo", "board")
    assert state is not None
    assert state.probe_pid is None
    assert state.lock_pid is None
    assert all(stage.state == "ok" for stage in state.stages.values())


@requires_sh
def test_execute_marks_failed_stage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    ctx, _probe = make_context(tmp_path)

    class FailingProbe(FakeProbe):
        def exec(self, request: ExecRequest):  # type: ignore[no-untyped-def]
            self.requests.append(request)
            yield ExecEvent(event="start", pid=1, pgid=1)
            for step in request.steps:
                yield ExecEvent(event="step_start", id=step.id)
                yield ExecEvent(event="step_end", id=step.id, code=2, tail="boom")
            yield ExecEvent(event="end", code=1)

    failing = FailingProbe()
    ctx.probe = failing  # type: ignore[assignment]
    pipeline = Pipeline(
        ctx.loaded, "board", executor=FakeExecutor(), console=quiet_console()
    )
    steps = [step for step in pipeline.build_steps() if step.kind != "sync"]
    with pytest.raises(PipelineError, match="step failed"):
        pipeline.execute(ctx, steps)
    state = load_state("demo", "board")
    assert state is not None
    assert any(stage.state == "failed" for stage in state.stages.values())
