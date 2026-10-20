from __future__ import annotations

import base64

import pytest

from ferry.config import BuildCfg, DeployCfg, TargetCfg
from ferry.executors import CommandResult
from ferry.facts import FactError, collect_target_info, parse_node_info
from ferry.models import ExecEvent


class FakeExecutor:
    def __init__(self, stdout: bytes = b"", code: int = 0) -> None:
        self.stdout = stdout
        self.code = code
        self.calls: list[tuple[str, list[str]]] = []

    def check(self, host: str) -> bool:
        return True

    def run(self, host, argv, stdin=None, timeout=None):  # type: ignore[no-untyped-def]
        self.calls.append((host, argv))
        return CommandResult(self.code, self.stdout, b"failed" if self.code else b"")

    def spawn(self, host, argv, stdin=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class FakeProbe:
    def __init__(self, stdout: bytes = b"", code: int = 0) -> None:
        self.stdout = stdout
        self.code = code
        self.requests = []

    def exec(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        for step in request.steps:
            yield ExecEvent(event="step_start", id=step.id)
            yield ExecEvent(
                event="out",
                id=step.id,
                stream="stdout",
                chunk=base64.b64encode(self.stdout).decode("ascii"),
            )
            yield ExecEvent(event="step_end", id=step.id, code=self.code)
        yield ExecEvent(event="end", code=self.code)


def make_target() -> TargetCfg:
    return TargetCfg(
        build=BuildCfg(node="build", workdir="/work/demo"),
        deploy=DeployCfg(node="board", path="/opt/app"),
        build_cmd="make",
    )


FACTS = b"""os=linux
arch=aarch64
kernel=5.10.1
distro=ubuntu
os_ver=18.04
init=systemd
glibc=2.27
has_tar=1
has_gzip=0
has_rsync=0
has_scp=1
"""


def test_parse_node_info_normalizes_arch() -> None:
    info = parse_node_info(FACTS.decode())
    assert info.arch == "arm64"
    assert info.os == "linux"
    assert info.distro == "ubuntu"
    assert info.init == "systemd"
    assert info.has_tar is True
    assert info.has_rsync is False


def test_collect_target_info_prefers_dev() -> None:
    executor = FakeExecutor(stdout=FACTS)
    probe = FakeProbe()
    info = collect_target_info(
        executor, make_target(), probe, dev_reachable=True  # type: ignore[arg-type]
    )
    assert info.arch == "arm64"
    assert executor.calls[0][0] == "board"
    assert not probe.requests


def test_collect_target_info_uses_probe_when_dev_cannot_reach() -> None:
    executor = FakeExecutor(code=255)
    probe = FakeProbe(stdout=FACTS)
    info = collect_target_info(
        executor, make_target(), probe, dev_reachable=False  # type: ignore[arg-type]
    )
    assert info.arch == "arm64"
    assert len(probe.requests) == 1
    step = probe.requests[0].steps[0]
    assert step.where == "target"
    assert probe.requests[0].target_host == "board"


def test_collect_target_info_reports_failure() -> None:
    executor = FakeExecutor(code=255)
    probe = FakeProbe(code=2)
    with pytest.raises(FactError, match="target.env"):
        collect_target_info(
            executor, make_target(), probe, dev_reachable=False  # type: ignore[arg-type]
        )
