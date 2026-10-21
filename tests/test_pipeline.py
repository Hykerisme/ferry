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
from ferry.pipeline import Pipeline, PipelineError


def make_loaded(tmp_path: Path) -> LoadedConfig:
    target = TargetCfg(
        build=BuildCfg(
            node="build",
            workdir="/work/demo",
            out=["./build/out", "dist/app"],
            before_build=["scripts/prep.sh"],
            run_build="make -j",
            after_build="echo done",
        ),
        deploy=DeployCfg(
            node="board",
            transfer_from="build.node",
            workdir="/opt/app",
            before_deploy="echo stop",
            run_deploy=["scripts/deploy.sh"],
            after_deploy=["scripts/restart.sh", "scripts/notify.sh"],
        ),
    )
    config = FerryConfig(
        project=ProjectCfg(name="demo"),
        targets={"board": target},
    )
    return LoadedConfig(config=config, root=tmp_path, path=tmp_path / ".ferry.yaml")


def test_plan_order_and_expansion(tmp_path: Path) -> None:
    loaded = make_loaded(tmp_path)
    pipeline = Pipeline(
        loaded, "board", console=Console(file=StringIO()), credentials=Credentials()
    )
    steps = pipeline.build_steps()
    assert [step.phase for step in steps] == [
        "before_build",
        "sync",
        "run_build",
        "after_build",
        "before_deploy",
        "transfer",
        "transfer",
        "run_deploy",
        "after_deploy",
        "after_deploy",
    ]
    assert steps[0].kind == "script"
    assert steps[2].kind == "command"
    assert steps[5].artifact == "./build/out"
    assert steps[6].artifact == "dist/app"
    assert steps[7].kind == "script"


def test_unknown_target_is_reported(tmp_path: Path) -> None:
    loaded = make_loaded(tmp_path)
    with pytest.raises(PipelineError, match="board"):
        Pipeline(
            loaded,
            "missing",
            console=Console(file=StringIO()),
            credentials=Credentials(),
        )
