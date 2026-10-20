from __future__ import annotations

from pathlib import Path

import pytest

from ferry.executors import CommandResult
from ferry.topology import (
    Edges,
    RoutePolicy,
    TopologyError,
    probe_edges,
)


class FakeExecutor:
    def __init__(self, dev_build: bool, build_target: bool, dev_target: bool = False) -> None:
        self.dev_build = dev_build
        self.build_target = build_target
        self.dev_target = dev_target
        self.calls = 0

    def check(self, host: str) -> bool:
        self.calls += 1
        if host == "build":
            return self.dev_build
        return self.dev_target

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        code = 0 if self.build_target else 1
        return CommandResult(code, b"", b"")

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def test_route_policy_prefers_direct_route() -> None:
    route = RoutePolicy().decide(Edges(dev_build=True, build_target=True, dev_target=True))
    assert route.name == "build->target"


def test_route_policy_falls_back_to_dev_relay() -> None:
    route = RoutePolicy().decide(Edges(dev_build=True, build_target=False, dev_target=True))
    assert route.name == "build->dev->target"
    assert route.uses_dev_relay is True


def test_route_policy_prefers_dev_relay_for_password_mode() -> None:
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
    edges = probe_edges(executor, "build", "target", project="p", target="t")
    assert edges.build_target is True
    first_calls = executor.calls

    cached = probe_edges(executor, "build", "target", project="p", target="t")
    assert cached == edges
    assert executor.calls == first_calls

    changed = FakeExecutor(dev_build=True, build_target=False)
    refreshed = probe_edges(changed, "build", "target", project="p", target="t", refresh=True)
    assert refreshed.build_target is False


def test_topology_cache_expiry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ferry.topology.cache_dir", lambda: tmp_path)
    executor = FakeExecutor(dev_build=True, build_target=True)
    probe_edges(executor, "build", "target", project="p", target="t", ttl=300)
    refreshed = probe_edges(
        executor, "build", "target", project="p", target="t", ttl=-1
    )
    assert refreshed.build_target is True
