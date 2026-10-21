from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ferry.config import BuildCfg
from ferry.executors import CommandResult
from ferry.nodes import NodeSpec
from ferry.topology import (
    Edges,
    RoutePolicy,
    TopologyError,
    check_build_to_target,
    probe_edges,
)


class FakeExecutor:
    def __init__(self, dev_build: bool, build_target: bool, dev_target: bool = False) -> None:
        self.dev_build = dev_build
        self.build_target = build_target
        self.dev_target = dev_target
        self.calls = 0
        self.last_args: list[str] = []

    def check(self, host) -> bool:  # type: ignore[no-untyped-def]
        self.calls += 1
        if host.role == "build":
            return self.dev_build
        return self.dev_target

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        self.last_args = list(argv)
        code = 0 if self.build_target else 1
        return CommandResult(code, b"", b"")

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


BUILD = NodeSpec(key="build", destination="build", role="build")
TARGET = NodeSpec(key="board", destination="board", role="deploy")


def test_route_policy_prefers_direct_route() -> None:
    route = RoutePolicy().decide(Edges(dev_build=True, build_target=True, dev_target=True))
    assert route.name == "build->target"


def test_route_policy_falls_back_to_dev_relay() -> None:
    route = RoutePolicy().decide(Edges(dev_build=True, build_target=False, dev_target=True))
    assert route.name == "build->dev->target"
    assert route.uses_dev_relay is True


def test_route_policy_prefers_dev_relay_when_requested() -> None:
    route = RoutePolicy().decide(
        Edges(dev_build=True, build_target=True, dev_target=True),
        prefer_via_dev=True,
    )
    assert route.name == "build->dev->target"


def test_route_policy_rejects_missing_edges() -> None:
    with pytest.raises(TopologyError):
        RoutePolicy().decide(Edges(dev_build=False, build_target=True, dev_target=True))
    with pytest.raises(TopologyError):
        RoutePolicy().decide(Edges(dev_build=True, build_target=False, dev_target=False))


def test_topology_cache_ttl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ferry.topology.cache_dir", lambda: tmp_path)
    executor = FakeExecutor(dev_build=True, build_target=True)
    edges = probe_edges(
        executor, BUILD, TARGET, project="p", target_name="t"
    )
    assert edges.build_target is True
    first_calls = executor.calls

    cached = probe_edges(
        executor, BUILD, TARGET, project="p", target_name="t"
    )
    assert cached == edges
    assert executor.calls == first_calls

    changed = FakeExecutor(dev_build=True, build_target=False)
    refreshed = probe_edges(
        changed, BUILD, TARGET, project="p", target_name="t", refresh=True
    )
    assert refreshed.build_target is False


def test_check_build_to_target_uses_inline_options() -> None:
    executor = FakeExecutor(dev_build=True, build_target=True)
    target = NodeSpec(
        key="board",
        destination="board",
        role="deploy",
        port="2222",
        user="root",
        options=("-o", "IdentityFile=/keys/id"),
    )
    assert check_build_to_target(executor, BUILD, target)
    command = executor.last_args
    assert "-p" in command
    assert "2222" in command
    assert "User=root" in command
    assert "IdentityFile=/keys/id" in command


def test_build_cannot_be_a_reference() -> None:
    with pytest.raises(ValidationError):
        BuildCfg(
            node="dev.node",
            workdir="/work",
            out="./dist",
            run_build="true",
        )
