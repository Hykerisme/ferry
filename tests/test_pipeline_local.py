from __future__ import annotations

from io import StringIO
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
from ferry.pipeline import Pipeline, PipelineError
from ferry.state import load_state


class RecordingExecutor:
    def __init__(self) -> None:
        self.checks: list[str] = []
        self.calls: list[tuple[str, object]] = []

    def check(self, host) -> bool:  # type: ignore[no-untyped-def]
        self.checks.append(host.key)
        return True

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        self.calls.append(("run", host))
        return CommandResult(0, b"", b"")

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        self.calls.append(("spawn", host))
        raise AssertionError("unexpected remote spawn")


def quiet_console() -> Console:
    return Console(file=StringIO())


def make_pipeline(
    tmp_path: Path,
    target: TargetCfg,
    executor: RecordingExecutor,
) -> Pipeline:
    loaded = LoadedConfig(
        config=FerryConfig(
            project=ProjectCfg(name="demo"), targets={"local": target}
        ),
        root=tmp_path,
        path=tmp_path / ".ferry.yaml",
    )
    return Pipeline(
        loaded,
        "local",
        executor=executor,  # type: ignore[arg-type]
        console=quiet_console(),
        credentials=Credentials(),
    )


def local_target(
    build_workdir: str = ".",
    out: str = "./dist",
    run_build: str = "mkdir -p dist && printf hi > dist/a.txt",
    deploy_workdir: str = "./deploy",
    run_deploy: str = "printf deployed > result.txt",
) -> TargetCfg:
    return TargetCfg(
        build=BuildCfg(
            node="dev.node",
            workdir=build_workdir,
            out=out,
            run_build=run_build,
        ),
        deploy=DeployCfg(
            node="dev.node",
            workdir=deploy_workdir,
            run_deploy=run_deploy,
        ),
    )


def test_local_in_place_build_and_deploy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    executor = RecordingExecutor()
    pipeline = make_pipeline(tmp_path, local_target(), executor)
    ctx = pipeline.prepare()
    steps = pipeline.build_steps(ctx)

    assert ctx.route.name == "dev->dev"
    assert ctx.build_is_local
    assert not any(step.kind == "sync" for step in steps)
    assert executor.checks == []

    pipeline.execute(ctx, steps)

    assert (tmp_path / "deploy" / "a.txt").read_text(encoding="utf-8") == "hi"
    assert (tmp_path / "deploy" / "result.txt").read_text(
        encoding="utf-8"
    ) == "deployed"
    assert not (tmp_path / ".ferry" / "local.marker").exists()
    state = load_state("demo", "local")
    assert state is not None
    assert all(stage.state == "ok" for stage in state.stages.values())


def test_local_copy_build(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    (tmp_path / "src.txt").write_text("source", encoding="utf-8")
    target = local_target(
        build_workdir="buildroot",
        run_build="mkdir -p dist && cat src.txt > dist/a.txt",
    )
    executor = RecordingExecutor()
    pipeline = make_pipeline(tmp_path, target, executor)
    ctx = pipeline.prepare()
    steps = pipeline.build_steps(ctx)
    assert any(step.kind == "sync" for step in steps)

    pipeline.execute(ctx, steps)

    assert (tmp_path / "buildroot" / "src.txt").read_text(
        encoding="utf-8"
    ) == "source"
    assert (tmp_path / "deploy" / "a.txt").read_text(encoding="utf-8") == "source"
    assert executor.checks == []


def test_local_build_external_deploy_prepares_route(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    target = TargetCfg(
        build=BuildCfg(
            node="dev.node",
            workdir=".",
            out="./dist",
            run_build="true",
        ),
        deploy=DeployCfg(
            node="board",
            workdir="/opt/app",
            run_deploy="true",
        ),
    )
    executor = RecordingExecutor()
    pipeline = make_pipeline(tmp_path, target, executor)
    ctx = pipeline.prepare()
    assert ctx.route.name == "dev->target"
    assert ctx.route.uses_dev_relay
    assert executor.checks == ["board"]


def test_deploy_on_build_becomes_local_with_local_build(
    tmp_path: Path,
) -> None:
    target = local_target()
    target.deploy.node = "build.node"
    pipeline = make_pipeline(tmp_path, target, RecordingExecutor())
    ctx = pipeline.prepare()
    assert ctx.resolved.deploy_role == "dev"
    assert ctx.resolved.warnings


def test_missing_artifact_fails_local_transfer(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    target = local_target(run_build="true")
    pipeline = make_pipeline(tmp_path, target, RecordingExecutor())
    ctx = pipeline.prepare()
    with pytest.raises(PipelineError, match="artifact not found"):
        pipeline.execute(ctx, pipeline.build_steps(ctx))
