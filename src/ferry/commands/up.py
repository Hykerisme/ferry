from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from ..auth import resolve_credentials
from ..bootstrap import BootstrapError, bootstrap_keys
from ..config import (
    CONFIG_FILENAME,
    BuildCfg,
    ConfigError,
    DeployCfg,
    StageScripts,
    SyncCfg,
    TargetCfg,
    ToolchainCfg,
    add_target,
    config_to_yaml,
    diff_config,
    initial_config,
    load_config,
)
from ..executors import ExecError, SshExecutor, explain_ssh_failure
from ..paths import identity_path
from ..pipeline import PipelineError, check_toolchain_version
from ..probe import ProbeError, ensure_remote_probe

console = Console()


def _pick(value: str | None, prompt: str, default: str | None = None) -> str:
    if value:
        return value
    if default:
        entered = typer.prompt(prompt, default=default)
    else:
        entered = typer.prompt(prompt)
    return str(entered).strip()


def _require_ssh(executor: SshExecutor, host: str, label: str) -> None:
    result = executor.run(host, ["true"], timeout=15)
    if result.returncode != 0:
        raise typer.BadParameter(explain_ssh_failure(host, result.stderr_text), param_hint=label)


def _require_writable(executor: SshExecutor, host: str, path: str, label: str) -> None:
    script = f"mkdir -p {shlex_quote(path)} && test -w {shlex_quote(path)}"
    result = executor.run(host, ["sh", "-c", script], timeout=20)
    if result.returncode != 0:
        raise typer.BadParameter(
            f"{label} {path!r} is not writable on {host}: {result.stderr_text.strip()}",
            param_hint=label,
        )


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def up(
    ssh: str | None = typer.Option(
        None, "--ssh", help="build node SSH target or ~/.ssh/config alias"
    ),
    target: str | None = typer.Option(None, "--target", help="target node SSH target"),
    name: str | None = typer.Option(None, "--name", help="target alias used in .ferry.yaml"),
    build_workdir: str | None = typer.Option(
        None, "--build-workdir", help="writable dir on the build node"
    ),
    deploy_path: str | None = typer.Option(
        None, "--deploy-path", help="writable dir on the target node"
    ),
    toolchain_probe: str | None = typer.Option(
        None, "--toolchain-probe", help="command printing the toolchain version"
    ),
    toolchain_expect: str | None = typer.Option(
        None, "--toolchain-expect", help="expected toolchain version"
    ),
    build_cmd: str | None = typer.Option(
        None, "--build-cmd", help="build command (run on the build node)"
    ),
    out: str | None = typer.Option(
        None, "--out", help="artifact directory relative to the build workdir"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="accept the generated config without prompting"
    ),
    reset: bool = typer.Option(
        False, "--reset", help="re-upload the probe binary to the build node"
    ),
    ask_pass: bool = typer.Option(
        False, "--ask-pass", help="prompt for SSH passwords and install Ferry keys"
    ),
) -> None:
    """Bootstrap a build/target pair and write it to .ferry.yaml."""
    try:
        loaded = load_config(Path.cwd())
        config = loaded.config
        root = loaded.root
    except ConfigError:
        root = Path.cwd()
        config = initial_config(root.name)
    config_path = root / CONFIG_FILENAME

    project = config.project.name or root.name
    build_node = _pick(ssh, "Build node SSH target")
    target_node = _pick(target, "Target node SSH target")
    target_name = _pick(name, "Target alias", default="default")
    existing = config.targets.get(target_name)
    workdir = _pick(
        build_workdir,
        "Writable directory on build node",
        default=existing.build.workdir if existing else f"/work/{project}",
    )
    deploy_dir = _pick(
        deploy_path,
        "Writable directory on target node",
        default=existing.deploy.path if existing else "/opt/app",
    )
    default_cmd = (
        existing.build_cmd
        if existing
        else (
            "cmake -B build && cmake --build build -j"
            if (root / "CMakeLists.txt").is_file()
            else "make -j"
        )
    )
    command = _pick(build_cmd, "Build command", default=default_cmd)
    artifact_dir = _pick(
        out, "Artifact directory", default=existing.out if existing else "dist"
    )
    probe_cmd = (
        toolchain_probe
        if toolchain_probe is not None
        else (existing.toolchain.probe if existing else None)
    )
    expected_version = (
        toolchain_expect
        if toolchain_expect is not None
        else (existing.toolchain.expect if existing else None)
    )

    if not workdir.startswith("/") or not deploy_dir.startswith("/"):
        console.print("[red]workdir and deploy path must be absolute paths[/red]")
        raise typer.Exit(1)

    credentials = resolve_credentials(ask=ask_pass)
    passwords: dict[str, str] = {}
    if credentials.build_password:
        passwords[build_node] = credentials.build_password
    if credentials.target_password:
        passwords[target_node] = credentials.target_password
    identity = str(identity_path()) if identity_path().is_file() else None
    executor = SshExecutor(
        accept_new_host_keys=True, passwords=passwords, identity=identity
    )
    build_key = existing.build.ssh_key if existing else None
    try:
        console.print(f"checking {build_node} ...")
        _require_ssh(executor, build_node, "--ssh")
        _require_writable(executor, build_node, workdir, "--build-workdir")
        console.print(f"checking {target_node} ...")
        _require_ssh(executor, target_node, "--target")
        _require_writable(executor, target_node, deploy_dir, "--deploy-path")

        if credentials.has_any:
            if credentials.build_password and credentials.target_password:
                try:
                    build_key = bootstrap_keys(
                        executor, build_node, target_node, credentials
                    )
                    console.print(
                        "[green]installed Ferry SSH keys[/green]; "
                        "future runs use key authentication"
                    )
                    executor = SshExecutor(
                        accept_new_host_keys=True, identity=str(identity_path())
                    )
                except BootstrapError as exc:
                    console.print(f"[yellow]key bootstrap skipped:[/yellow] {exc}")
            else:
                console.print(
                    "[yellow]supplying only one password; "
                    "no key bootstrap performed[/yellow]"
                )

        probe = ensure_remote_probe(executor, build_node, workdir, reset=reset)
        info = probe.info()
        console.print(
            f"build node: {info.distro or info.os} {info.os_ver} {info.arch} "
            f"init={info.init or 'unknown'} glibc={info.glibc or 'n/a'}"
        )
        if probe_cmd:
            version = check_toolchain_version(
                probe, build_node, workdir, probe_cmd, expected_version
            )
            console.print(f"toolchain: {version or '(no output)'}")
    except (ExecError, ProbeError, PipelineError) as exc:
        console.print(f"[red]bootstrap failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    if expected_version and not probe_cmd:
        console.print("[red]--toolchain-expect requires --toolchain-probe[/red]")
        raise typer.Exit(1)

    target_config = TargetCfg(
        build=BuildCfg(
            node=build_node,
            workdir=workdir,
            clean=existing.build.clean if existing else None,
            ssh_key=build_key,
        ),
        deploy=DeployCfg(node=target_node, path=deploy_dir),
        toolchain=ToolchainCfg(probe=probe_cmd, expect=expected_version),
        build_cmd=command,
        out=artifact_dir,
        stages=existing.stages if existing else StageScripts(),
        sync=existing.sync if existing else SyncCfg(),
    )
    new_config = add_target(
        config, target_name, target_config, replace=existing is not None
    )
    new_text = config_to_yaml(new_config)
    if config_path.is_file():
        old_text = config_path.read_text(encoding="utf-8")
        diff = diff_config(old_text, new_text)
        if diff:
            console.print(diff, markup=False, highlight=False)
    else:
        console.print(new_text, markup=False, highlight=False)

    if not yes and config_path.is_file():
        typer.confirm("Write this configuration?", abort=True)
    config_path.write_text(new_text, encoding="utf-8")
    console.print(f"wrote {config_path}")
    console.print(f"next: ferry plan {target_name}")
