from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, cast

import typer
from rich.console import Console

from ..auth import resolve_credentials
from ..bootstrap import BootstrapError, bootstrap_keys, ensure_local_key, install_public_key
from ..config import (
    CONFIG_FILENAME,
    PRIVATE_CONFIG_FILENAME,
    BuildCfg,
    ConfigError,
    DeployCfg,
    InlineNode,
    SyncCfg,
    TargetCfg,
    config_to_yaml,
    diff_config,
    initial_config,
    load_config,
)
from ..executors import ExecError, SshExecutor, explain_ssh_failure
from ..nodes import NodeSpec, resolve_target
from ..paths import identity_path
from ..private_config import dump_document, ensure_gitignore, load_document, regenerate_public
from ..probe import ProbeError, ensure_remote_probe

console = Console()


def _pick(value: str | None, prompt: str, default: str | None = None) -> str:
    if value:
        return value
    entered = typer.prompt(prompt, default=default) if default else typer.prompt(prompt)
    return str(entered).strip()


def _node_input(
    alias: str | None,
    host: str | None,
    user: str | None,
    port: str | None,
    existing: str | InlineNode | None,
    label: str,
) -> str | InlineNode:
    if host:
        return InlineNode(hostname=host, user=user, port=port)
    if alias:
        return alias
    if existing is not None:
        default = existing if isinstance(existing, str) else ""
    else:
        default = ""
    entered = str(
        typer.prompt(
            f"{label} SSH alias (empty keeps current / use --{label}-host for inline)",
            default=default,
        )
    ).strip()
    if entered:
        return entered
    if existing is not None:
        return existing
    raise typer.BadParameter(f"{label} node is required")


def _command_default(value: str | list[str] | None, fallback: str) -> str:
    if isinstance(value, str):
        return value
    return fallback


def _require_ssh(executor: SshExecutor, node: NodeSpec, label: str) -> None:
    result = executor.run(node, ["true"], timeout=15)
    if result.returncode != 0:
        raise typer.BadParameter(
            explain_ssh_failure(node, result.stderr_text), param_hint=label
        )


def _require_writable(
    executor: SshExecutor | None, node: NodeSpec | None, path: str, label: str
) -> None:
    if node is None:
        local = Path(path)
        local.mkdir(parents=True, exist_ok=True)
        if not os.access(local, os.W_OK):
            raise typer.BadParameter(f"{label} {path!r} is not writable")
        return
    if executor is None:
        raise typer.BadParameter(f"no executor for {label}")
    script = f"mkdir -p {shlex_quote(path)} && test -w {shlex_quote(path)}"
    result = executor.run(node, ["sh", "-c", script], timeout=20)
    if result.returncode != 0:
        raise typer.BadParameter(
            f"{label} {path!r} is not writable on {node.display()}: "
            f"{result.stderr_text.strip()}",
            param_hint=label,
        )


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _set_or_remove(mapping: Any, key: str, value: Any) -> None:
    if value is None:
        mapping.pop(key, None)
    else:
        mapping[key] = value


def up(
    ssh: str | None = typer.Option(None, "--ssh", help="build node SSH alias"),
    build_host: str | None = typer.Option(None, "--build-host", help="inline build hostname"),
    build_user: str | None = typer.Option(None, "--build-user", help="inline build user"),
    build_port: str | None = typer.Option(None, "--build-port", help="inline build port"),
    target: str | None = typer.Option(
        None, "--target", help="deploy alias or dev.node/build.node"
    ),
    target_host: str | None = typer.Option(
        None, "--target-host", help="inline deploy hostname"
    ),
    target_user: str | None = typer.Option(None, "--target-user", help="inline deploy user"),
    target_port: str | None = typer.Option(None, "--target-port", help="inline deploy port"),
    name: str | None = typer.Option(None, "--name", help="target alias in .ferry.yaml"),
    build_workdir: str | None = typer.Option(
        None, "--build-workdir", help="absolute writable dir on build"
    ),
    out: list[str] = typer.Option(  # noqa: B008
        [], "--out", help="artifact path relative to build.workdir (repeatable)"
    ),
    run_build: str | None = typer.Option(None, "--run-build", help="build command"),
    run_deploy: str | None = typer.Option(None, "--run-deploy", help="deploy command"),
    deploy_workdir: str | None = typer.Option(
        None, "--deploy-workdir", help="absolute dir on deploy"
    ),
    transfer_from: str | None = typer.Option(
        None, "--transfer-from", help="dev.node or build.node"
    ),
    before_build: str | None = typer.Option(None, "--before-build"),
    after_build: str | None = typer.Option(None, "--after-build"),
    before_deploy: str | None = typer.Option(None, "--before-deploy"),
    after_deploy: str | None = typer.Option(None, "--after-deploy"),
    yes: bool = typer.Option(False, "--yes", "-y", help="accept without prompting"),
    reset: bool = typer.Option(False, "--reset", help="re-upload the probe"),
    ask_pass: bool = typer.Option(
        False, "--ask-pass", help="prompt for SSH passwords and install Ferry keys"
    ),
) -> None:
    """Bootstrap a build/deploy pair and write .private.ferry.yaml."""
    try:
        loaded = load_config(Path.cwd())
        config = loaded.config
        root = loaded.root
    except ConfigError:
        root = Path.cwd()
        config = initial_config(root.name)

    project = config.project.name or root.name
    default_name = next(iter(config.targets)) if len(config.targets) == 1 else "default"
    target_name = _pick(name, "Target nickname", default=default_name)
    existing = config.targets.get(target_name)

    build_node = _node_input(
        ssh,
        build_host,
        build_user,
        build_port,
        existing.build.node if existing else None,
        "build",
    )
    deploy_node = _node_input(
        target,
        target_host,
        target_user,
        target_port,
        existing.deploy.node if existing else None,
        "target",
    )

    workdir = _pick(
        build_workdir,
        "Writable directory on build",
        default=(
            existing.build.workdir if existing else f"/work/{project}"
        ),
    )
    if out:
        out_entries: str | list[str] = out[0] if len(out) == 1 else list(out)
    elif existing is not None:
        out_entries = existing.build.out
    else:
        entered = _pick(None, "Artifact path", default="./dist")
        out_entries = entered

    command = _pick(
        run_build,
        "Build command",
        default=(
            _command_default(
                existing.build.run_build if existing else None,
                "cmake -B build && cmake --build build -j"
                if (root / "CMakeLists.txt").is_file()
                else "make -j",
            )
        ),
    )
    deploy_dir = _pick(
        deploy_workdir,
        "Deploy directory",
        default=existing.deploy.workdir if existing else "/opt/app",
    )
    deploy_command = _pick(
        run_deploy,
        "Deploy command",
        default=_command_default(
            existing.deploy.run_deploy if existing else None, "echo deployed"
        ),
    )

    before_build_value = before_build or (
        existing.build.before_build if existing else None
    )
    after_build_value = after_build or (existing.build.after_build if existing else None)
    before_deploy_value = before_deploy or (
        existing.deploy.before_deploy if existing else None
    )
    after_deploy_value = after_deploy or (
        existing.deploy.after_deploy if existing else None
    )
    transfer_value = transfer_from or (
        existing.deploy.transfer_from if existing else None
    )
    if transfer_value not in {None, "dev.node", "build.node"}:
        raise typer.BadParameter("--transfer-from must be dev.node or build.node")

    try:
        target_config = TargetCfg(
            sync=existing.sync if existing else SyncCfg(),
            build=BuildCfg(
                node=build_node,
                workdir=workdir,
                out=out_entries,
                before_build=before_build_value,
                run_build=command,
                after_build=after_build_value,
            ),
            deploy=DeployCfg(
                node=deploy_node,
                transfer_from=cast(
                    Literal["dev.node", "build.node"] | None, transfer_value
                ),
                workdir=deploy_dir,
                before_deploy=before_deploy_value,
                run_deploy=deploy_command,
                after_deploy=after_deploy_value,
            ),
        )
    except Exception as exc:
        console.print(f"[red]invalid configuration:[/red] {exc}")
        raise typer.Exit(1) from exc

    try:
        resolved = resolve_target(target_config, root)
    except Exception as exc:
        console.print(f"[red]cannot resolve nodes:[/red] {exc}")
        raise typer.Exit(1) from exc
    for warning in resolved.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")

    credentials = resolve_credentials(
        ask=ask_pass,
        need_build=not resolved.build_is_local,
        need_target=resolved.deploy_role == "external",
    )
    passwords: dict[str, str] = {}
    if credentials.build_password:
        passwords["build"] = credentials.build_password
    if credentials.target_password:
        passwords["deploy"] = credentials.target_password
    identity = str(identity_path()) if identity_path().is_file() else None
    executor = SshExecutor(
        accept_new_host_keys=True, passwords=passwords, identity=identity
    )

    try:
        if resolved.build_is_local:
            console.print("checking local build directory ...")
            _require_writable(
                None, None, resolved.build_workdir, "--build-workdir"
            )
        else:
            console.print(f"checking build {resolved.build.display()} ...")
            _require_ssh(executor, resolved.build, "--ssh")
            _require_writable(
                executor, resolved.build, resolved.build_workdir, "--build-workdir"
            )

        if resolved.deploy_role == "external":
            console.print(f"checking deploy {resolved.deploy.display()} ...")
            _require_ssh(executor, resolved.deploy, "--target")
            _require_writable(
                executor, resolved.deploy, resolved.deploy_workdir, "--deploy-workdir"
            )
        elif resolved.deploy_role == "build":
            _require_writable(
                executor, resolved.build, resolved.deploy_workdir, "--deploy-workdir"
            )
        else:
            _require_writable(
                None, None, resolved.deploy_workdir, "--deploy-workdir"
            )

        if credentials.has_any and not resolved.build_is_local:
            try:
                if (
                    resolved.deploy_role == "external"
                    and credentials.build_password
                    and credentials.target_password
                ):
                    bootstrap_keys(
                        executor, resolved.build, resolved.deploy, credentials
                    )
                    console.print(
                        "[green]installed Ferry SSH keys[/green]; "
                        "future runs use key authentication"
                    )
                    executor = SshExecutor(
                        accept_new_host_keys=True, identity=str(identity_path())
                    )
                elif credentials.build_password:
                    public = ensure_local_key()
                    install_public_key(executor, resolved.build, public)
                    console.print(
                        "[yellow]installed the dev key on build only; "
                        "target key setup skipped[/yellow]"
                    )
            except BootstrapError as exc:
                console.print(f"[yellow]key bootstrap skipped:[/yellow] {exc}")

        if resolved.build_is_local:
            console.print("local build: no probe installation required")
        else:
            probe = ensure_remote_probe(
                executor, resolved.build, resolved.build_workdir, reset=reset
            )
            console.print(f"probe installed at {probe.path}")
    except (ExecError, ProbeError) as exc:
        console.print(f"[red]bootstrap failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    new_targets = dict(config.targets)
    new_targets[target_name] = target_config
    new_config = config.model_copy(update={"targets": new_targets})

    private_path = root / PRIVATE_CONFIG_FILENAME
    public_path = root / CONFIG_FILENAME
    if private_path.is_file():
        document = load_document(private_path)
    elif public_path.is_file():
        document = load_document(public_path)
    else:
        document = {"version": 1, "project": {"name": project}, "targets": {}}
    if document is None:
        document = {"version": 1, "project": {"name": project}, "targets": {}}
    targets_doc = document.setdefault("targets", {})
    target_doc = targets_doc.setdefault(target_name, {})

    build_doc = target_doc.setdefault("build", {})
    build_doc["node"] = _node_to_document(target_config.build.node)
    build_doc["workdir"] = target_config.build.workdir
    build_doc["out"] = target_config.build.out
    _set_or_remove(build_doc, "before_build", target_config.build.before_build)
    build_doc["run_build"] = target_config.build.run_build
    _set_or_remove(build_doc, "after_build", target_config.build.after_build)

    deploy_doc = target_doc.setdefault("deploy", {})
    deploy_doc["node"] = _node_to_document(target_config.deploy.node)
    _set_or_remove(deploy_doc, "transfer_from", target_config.deploy.transfer_from)
    deploy_doc["workdir"] = target_config.deploy.workdir
    _set_or_remove(deploy_doc, "before_deploy", target_config.deploy.before_deploy)
    deploy_doc["run_deploy"] = target_config.deploy.run_deploy
    _set_or_remove(deploy_doc, "after_deploy", target_config.deploy.after_deploy)

    old_public = public_path.read_text(encoding="utf-8") if public_path.is_file() else ""
    if document.get("project") is None:
        document["project"] = {"name": project}
    if not yes:
        proposed = config_to_yaml(new_config)
        diff = diff_config(old_public, proposed)
        if diff:
            console.print(diff, markup=False, highlight=False)
        typer.confirm("Write these node settings?", abort=True)
    dump_document(document, private_path)
    regenerate_public(private_path, public_path)
    added = ensure_gitignore(root)
    if added:
        console.print(f"added to .gitignore: {', '.join(added)}")
    console.print(f"wrote {private_path}")
    console.print(f"generated {public_path} (node definitions removed)")
    console.print(f"next: ferry plan {target_name}")


def _node_to_document(value: str | InlineNode | None) -> Any:
    if isinstance(value, InlineNode):
        return value.model_dump(mode="json", exclude_none=True)
    return value
