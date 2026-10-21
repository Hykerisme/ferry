from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import InlineNode, NodeConfigMissing, TargetCfg

Role = Literal["dev", "build", "deploy"]
DeployRole = Literal["external", "dev", "build"]
TransferFrom = Literal["build", "dev"]


@dataclass(frozen=True)
class NodeSpec:
    key: str
    destination: str
    role: Role
    user: str | None = None
    port: str | None = None
    options: tuple[str, ...] = ()

    @property
    def is_local(self) -> bool:
        return self.role == "dev"

    def ssh_options(self) -> list[str]:
        result: list[str] = []
        if self.port:
            result.extend(["-p", self.port])
        if self.user:
            result.extend(["-o", f"User={self.user}"])
        result.extend(self.options)
        return result

    def display(self) -> str:
        if self.is_local:
            return "dev (local)"
        return self.destination


@dataclass(frozen=True)
class ResolvedTarget:
    build: NodeSpec
    deploy: NodeSpec
    deploy_role: DeployRole
    transfer_from: TransferFrom | None = None
    warnings: tuple[str, ...] = ()
    build_is_local: bool = False
    build_workdir: str = ""
    deploy_workdir: str = ""


def _alias_spec(alias: str, role: Role) -> NodeSpec:
    return NodeSpec(key=alias, destination=alias, role=role)


def _inline_spec(node: InlineNode, role: Role) -> NodeSpec:
    extras = dict(node.model_extra or {})
    hostname = node.resolved_name()
    user = node.user
    port = node.port
    for key in list(extras):
        lowered = key.lower()
        if lowered == "user" and user is None:
            user = extras.pop(key)
        elif lowered == "port" and port is None:
            port = extras.pop(key)
        elif lowered in {"hostname", "host"}:
            extras.pop(key)
        elif lowered in {"match", "include"}:
            raise NodeConfigMissing(f"{key!r} is not allowed in an inline node")
    options: list[str] = []
    for key, value in extras.items():
        if key.lower() in {"host", "match", "include"}:
            raise NodeConfigMissing(f"{key!r} is not allowed in an inline node")
        options.extend(["-o", f"{key}={value}"])
    user_text = str(user) if user is not None else None
    port_text = str(port) if port is not None else None
    prefix = f"{user_text}@" if user_text else ""
    suffix = f":{port_text}" if port_text else ""
    key = f"inline:{prefix}{hostname}{suffix}"
    return NodeSpec(
        key=key,
        destination=hostname,
        role=role,
        user=user_text,
        port=port_text,
        options=tuple(options),
    )


def _spec(node: str | InlineNode, role: Role) -> NodeSpec:
    if isinstance(node, str):
        return _alias_spec(node, role)
    return _inline_spec(node, role)


def _local_workdir(root: Path, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve().as_posix()


def resolve_target(target: TargetCfg, root: Path | None = None) -> ResolvedTarget:
    if target.build.node is None:
        raise NodeConfigMissing(
            "missing build.node; run 'ferry up' or add it to .private.ferry.yaml"
        )
    if target.deploy.node is None:
        raise NodeConfigMissing(
            "missing deploy.node; run 'ferry up' or add it to .private.ferry.yaml"
        )

    build_is_local = isinstance(target.build.node, str) and target.build.node == "dev.node"
    if build_is_local:
        if root is None:
            raise NodeConfigMissing(
                "project root is required to resolve a local build workdir"
            )
        build = NodeSpec(key="dev.node", destination="", role="dev")
        build_workdir = _local_workdir(root, target.build.workdir)
    else:
        build = _spec(target.build.node, "build")
        build_workdir = target.build.workdir

    warnings: list[str] = []
    deploy_role: DeployRole = "external"
    transfer_from: TransferFrom | None = None

    raw_deploy = target.deploy.node
    if isinstance(raw_deploy, str) and raw_deploy == "dev.node":
        deploy = NodeSpec(key="dev.node", destination="", role="dev")
        deploy_role = "dev"
    elif isinstance(raw_deploy, str) and raw_deploy == "build.node":
        if build_is_local:
            deploy = NodeSpec(key="dev.node", destination="", role="dev")
            deploy_role = "dev"
            warnings.append(
                "deploy.node=build.node but build.node is dev.node; deploying locally"
            )
        else:
            deploy = NodeSpec(
                key=build.key,
                destination=build.destination,
                role="build",
                user=build.user,
                port=build.port,
                options=build.options,
            )
            deploy_role = "build"
    else:
        deploy = _spec(raw_deploy, "deploy")

    if deploy.is_local:
        if root is None:
            raise NodeConfigMissing(
                "project root is required to resolve a local deploy workdir"
            )
        deploy_workdir = _local_workdir(root, target.deploy.workdir)
    else:
        deploy_workdir = target.deploy.workdir

    requested = target.deploy.transfer_from
    if requested is not None:
        mapped: TransferFrom = "build" if requested == "build.node" else "dev"
        if build_is_local:
            warnings.append(
                "build.node is dev.node; transfer_from has no effect in local mode"
            )
        elif deploy_role != "external":
            warnings.append(
                f"deploy.node is {raw_deploy}; transfer_from={requested} is ignored"
            )
        else:
            transfer_from = mapped
    return ResolvedTarget(
        build=build,
        deploy=deploy,
        deploy_role=deploy_role,
        transfer_from=transfer_from,
        warnings=tuple(warnings),
        build_is_local=build_is_local,
        build_workdir=build_workdir,
        deploy_workdir=deploy_workdir,
    )
