from __future__ import annotations

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
from ferry.models import NodeInfo
from ferry.pipeline import Pipeline, PipelineError, build_environment


def make_loaded(tmp_path: Path) -> LoadedConfig:
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
    config = FerryConfig(
        project=ProjectCfg(name="demo"),
        targets={"board": target},
    )
    return LoadedConfig(config=config, root=tmp_path, path=tmp_path / ".ferry.yaml")


def test_plan_order_and_locations(tmp_path: Path) -> None:
    loaded = make_loaded(tmp_path)
    pipeline = Pipeline(loaded, "board", console=Console(file=StringIO()))
    steps = pipeline.build_steps()
    assert [step.stage for step in steps] == [
        "pre_sync",
        "sync",
        "post_sync",
        "pre_build",
        "build",
        "post_build",
        "pre_deploy",
        "deploy",
        "post_deploy",
    ]
    assert [(step.stage, step.where) for step in steps if step.script] == [
        ("pre_sync", "dev"),
        ("post_sync", "build"),
        ("pre_build", "build"),
        ("post_build", "build"),
        ("pre_deploy", "target"),
        ("post_deploy", "target"),
    ]
    assert steps[1].kind == "sync"
    assert steps[4].command == "make -j"
    assert steps[8].script is not None


def test_unknown_target_is_reported(tmp_path: Path) -> None:
    loaded = make_loaded(tmp_path)
    with pytest.raises(PipelineError):
        Pipeline(loaded, "nope", console=Console(file=StringIO()))


def test_build_environment_uses_probe_facts() -> None:
    info = NodeInfo(
        os="linux",
        arch="arm64",
        kernel="5.10",
        distro="ubuntu",
        os_ver="18.04",
        init="systemd",
        glibc="2.27",
        has_tar=True,
        has_scp=True,
    )
    env = build_environment("demo", "board", info, "v2.4.1")
    assert env["XB_TARGET_ARCH"] == "arm64"
    assert env["XB_TARGET_OS"] == "ubuntu"
    assert env["XB_TARGET_INIT"] == "systemd"
    assert env["XB_TARGET_HAS_RSYNC"] == "0"
    assert env["XB_BUILD_TOOLCHAIN_VER"] == "v2.4.1"
    assert "XB_TARGET_GLIBC" in env


def test_unknown_target_lists_configured_targets(tmp_path: Path) -> None:
    loaded = make_loaded(tmp_path)
    with pytest.raises(PipelineError, match="board"):
        Pipeline(loaded, "missing", console=Console(file=StringIO()))
