from __future__ import annotations

import subprocess

from .auth import Credentials
from .executors import Executor, SshExecutor
from .paths import identity_path


class BootstrapError(Exception):
    pass


def ensure_local_key() -> str:
    """Return the public key of Ferry's managed dev keypair, creating it if needed."""
    path = identity_path()
    public = path.with_name(path.name + ".pub")
    if not path.is_file() or not public.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        completed = subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "ferry",
                "-f",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise BootstrapError(
                "failed to generate ~/.ferry/keys/id_ed25519: "
                f"{completed.stderr.strip()}"
            )
    try:
        return public.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BootstrapError(f"cannot read {public}: {exc}") from exc


def install_public_key(executor: Executor, host: str, public_key: str) -> None:
    command = (
        'key=$(cat); mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" && '
        'touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys" && '
        '{ grep -qxF "$key" "$HOME/.ssh/authorized_keys" 2>/dev/null || '
        'printf \'%s\\n\' "$key" >> "$HOME/.ssh/authorized_keys"; }'
    )
    result = executor.run(
        host, ["sh", "-c", command], stdin=(public_key + "\n").encode("utf-8"), timeout=30
    )
    if result.returncode != 0:
        raise BootstrapError(
            f"failed to install the public key on {host}: "
            f"{result.stderr_text.strip() or f'exit {result.returncode}'}"
        )


def ensure_build_key(executor: Executor, host: str) -> tuple[str, str]:
    home_result = executor.run(host, ["sh", "-c", 'printf %s "$HOME"'], timeout=15)
    home = home_result.stdout_text.strip()
    if home_result.returncode != 0 or not home:
        raise BootstrapError(
            f"cannot determine HOME on {host}: {home_result.stderr_text.strip()}"
        )
    command = (
        'mkdir -p "$HOME/.ferry/keys" && chmod 700 "$HOME/.ferry/keys" && '
        '(test -f "$HOME/.ferry/keys/id_ed25519.pub" || '
        'ssh-keygen -q -t ed25519 -N "" -C ferry -f "$HOME/.ferry/keys/id_ed25519") && '
        'cat "$HOME/.ferry/keys/id_ed25519.pub"'
    )
    result = executor.run(host, ["sh", "-c", command], timeout=60)
    if result.returncode != 0:
        raise BootstrapError(
            f"failed to prepare the build key on {host}: "
            f"{result.stderr_text.strip() or 'ssh-keygen is required'}"
        )
    lines = [line.strip() for line in result.stdout_text.splitlines() if line.strip()]
    if not lines:
        raise BootstrapError(f"build key on {host} produced no public key")
    return f"{home.rstrip('/')}/.ferry/keys/id_ed25519", lines[-1]


def bootstrap_keys(
    executor: Executor,
    build_node: str,
    target_node: str,
    credentials: Credentials,
) -> str:
    """Install keys so that future runs need no passwords. Returns the build key path."""
    if not credentials.build_password or not credentials.target_password:
        raise BootstrapError(
            "automatic key setup needs passwords for both build and target"
        )
    local_public = ensure_local_key()
    install_public_key(executor, build_node, local_public)
    install_public_key(executor, target_node, local_public)

    build_key, build_public = ensure_build_key(executor, build_node)
    install_public_key(executor, target_node, build_public)

    key_executor = SshExecutor(identity=str(identity_path()))
    if not key_executor.check(build_node):
        raise BootstrapError(f"key authentication still fails for {build_node}")
    if not key_executor.check(target_node):
        raise BootstrapError(f"key authentication still fails for {target_node}")
    result = key_executor.run(
        build_node,
        [
            "ssh",
            "-i",
            build_key,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=accept-new",
            target_node,
            "true",
        ],
        timeout=20,
    )
    if result.returncode != 0:
        raise BootstrapError(
            f"build cannot authenticate to target with the installed key: "
            f"{result.stderr_text.strip()}"
        )
    return build_key
