from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ferry.config import BuildCfg, DeployCfg, InlineNode, TargetCfg
from ferry.nodes import resolve_target


def make_target(
    build_node: str | InlineNode = "buildvm",
    deploy_node: str | InlineNode = "board",
    transfer_from: str | None = None,
) -> TargetCfg:
    return TargetCfg(
        build=BuildCfg(
            node=build_node,
            workdir="/work/demo",
            out="./dist",
            run_build="make",
        ),
        deploy=DeployCfg(
            node=deploy_node,
            transfer_from=transfer_from,  # type: ignore[arg-type]
            workdir="/opt/app",
            run_deploy="true",
        ),
    )


def test_alias_nodes() -> None:
    resolved = resolve_target(make_target())
    assert resolved.build.destination == "buildvm"
    assert resolved.deploy.destination == "board"
    assert resolved.deploy_role == "external"
    assert resolved.transfer_from is None


def test_inline_node_options() -> None:
    node = InlineNode(
        hostname="localhost",
        user="root",
        port=2222,
        **{"IdentityFile": "/keys/id"},
    )
    resolved = resolve_target(make_target(build_node=node))
    assert resolved.build.destination == "localhost"
    assert resolved.build.user == "root"
    assert resolved.build.port == "2222"
    options = resolved.build.ssh_options()
    assert "-p" in options and "2222" in options
    assert "User=root" in options
    assert "IdentityFile=/keys/id" in options


def test_deploy_dev_reference(tmp_path: Path) -> None:
    resolved = resolve_target(make_target(deploy_node="dev.node"), tmp_path)
    assert resolved.deploy_role == "dev"
    assert resolved.deploy.is_local
    assert resolved.deploy_workdir.endswith("/opt/app")


def test_deploy_build_reference_reuses_build_spec() -> None:
    resolved = resolve_target(make_target(deploy_node="build.node"))
    assert resolved.deploy_role == "build"
    assert resolved.deploy.destination == resolved.build.destination


def test_transfer_from_is_route_selection() -> None:
    resolved = resolve_target(make_target(transfer_from="dev.node"))
    assert resolved.transfer_from == "dev"
    assert resolved.deploy_role == "external"


def test_transfer_from_ignored_for_local_deploy() -> None:
    resolved = resolve_target(make_target(deploy_node="build.node", transfer_from="dev.node"))
    assert resolved.transfer_from is None
    assert resolved.warnings


def test_build_reference_is_rejected_by_schema() -> None:
    with pytest.raises(ValidationError):
        make_target(build_node="build.node")
