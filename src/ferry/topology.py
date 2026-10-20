from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .executors import Executor
from .paths import cache_dir, safe_name

CACHE_TTL_SECONDS = 300
SSH_CHECK_OPTIONS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=5",
    "-o",
    "StrictHostKeyChecking=accept-new",
]


class TopologyError(Exception):
    pass


@dataclass(frozen=True)
class Edges:
    dev_build: bool
    build_target: bool
    dev_target: bool


@dataclass(frozen=True)
class Route:
    name: str
    description: str
    uses_dev_relay: bool = False


class RoutePolicy:
    def decide(self, edges: Edges) -> Route:
        if not edges.dev_build:
            raise TopologyError("dev cannot reach the build node over SSH")
        if edges.build_target:
            return Route(
                name="build->target",
                description="artifacts are pushed directly from build to target",
            )
        raise TopologyError(
            "build cannot reach target over SSH; the build->dev->target fallback "
            "is planned for v2. Make the build node able to ssh to the target, "
            "or verify the target alias in ~/.ssh/config."
        )


def _cache_path(project: str, target: str) -> Path:
    return cache_dir() / "topology" / safe_name(project) / f"{safe_name(target)}.json"


def _load_cache(project: str, target: str, ttl: int) -> Edges | None:
    path = _cache_path(project, target)
    if not path.is_file():
        return None
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(payload["timestamp"]) > ttl:
            return None
        return Edges(
            dev_build=bool(payload["dev_build"]),
            build_target=bool(payload["build_target"]),
            dev_target=bool(payload["dev_target"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_cache(project: str, target: str, edges: Edges) -> None:
    path = _cache_path(project, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": time.time(), **asdict(edges)}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def check_build_to_target(executor: Executor, build_node: str, target_node: str) -> bool:
    argv = ["ssh", *SSH_CHECK_OPTIONS, target_node, "true"]
    try:
        result = executor.run(build_node, argv, timeout=15)
    except Exception:
        return False
    return result.returncode == 0


def probe_edges(
    executor: Executor,
    build_node: str,
    target_node: str,
    *,
    project: str,
    target: str,
    refresh: bool = False,
    ttl: int = CACHE_TTL_SECONDS,
) -> Edges:
    if not refresh:
        cached = _load_cache(project, target, ttl)
        if cached is not None:
            return cached
    dev_build = executor.check(build_node)
    dev_target = executor.check(target_node) if dev_build else False
    build_target = check_build_to_target(executor, build_node, target_node) if dev_build else False
    edges = Edges(dev_build=dev_build, build_target=build_target, dev_target=dev_target)
    if dev_build:
        _save_cache(project, target, edges)
    return edges
