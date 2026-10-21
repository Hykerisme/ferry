from __future__ import annotations

import fnmatch
import posixpath
import shlex
import shutil
import tarfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

from .executors import ExecError, Executor, Host
from .nodes import NodeSpec


def is_ignored(relative: str, patterns: list[str]) -> bool:
    parts = PurePosixPath(relative).parts
    for raw in patterns:
        pattern = raw.strip().replace("\\", "/").rstrip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if fnmatch.fnmatch(relative, pattern) or relative.startswith(pattern + "/"):
                return True
        else:
            if any(fnmatch.fnmatch(part, pattern) for part in parts):
                return True
    return False


def first_component(value: str) -> str:
    parts = PurePosixPath(value.replace("\\", "/")).parts
    return parts[0] if parts else value


def effective_ignores(patterns: list[str], outs: str | list[str]) -> list[str]:
    ignores = list(patterns)
    for mandatory in (".private.ferry.yaml", ".env"):
        if mandatory not in ignores:
            ignores.append(mandatory)
    entries = [outs] if isinstance(outs, str) else outs
    for entry in entries:
        top = first_component(entry)
        if top and top not in ignores:
            ignores.append(top)
    return ignores


def _iter_entries(root: Path, patterns: list[str]) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if is_ignored(rel, patterns):
            continue
        entries.append((path, rel))
    return entries


def _normalized_info(tar: tarfile.TarFile, path: Path, arcname: str) -> tarfile.TarInfo:
    info = tar.gettarinfo(str(path), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    if info.isdir():
        info.mode = 0o755
    elif info.issym() or info.islnk():
        info.mode = 0o777
    else:
        info.mode = 0o644
    if info.islnk():
        info.linkname = info.linkname.replace("\\", "/")
    return info


def write_tar(stream: IO[bytes], root: Path, patterns: list[str]) -> int:
    count = 0
    with tarfile.open(fileobj=stream, mode="w|") as tar:
        for path, rel in _iter_entries(root, patterns):
            info = _normalized_info(tar, path, rel)
            if info.isreg():
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
            elif info.isdir():
                tar.addfile(info)
            elif info.issym():
                tar.addfile(info)
            count += 1
    return count


def _add_tar_path(tar: tarfile.TarFile, path: Path, arcname: str) -> None:
    info = _normalized_info(tar, path, arcname)
    if info.isreg():
        with path.open("rb") as handle:
            tar.addfile(info, handle)
    elif info.isdir():
        tar.addfile(info)
        for child in sorted(path.iterdir()):
            _add_tar_path(tar, child, f"{arcname}/{child.name}")
    elif info.issym() or info.islnk():
        tar.addfile(info)


def write_tar_paths(
    stream: IO[bytes], root: Path, paths: list[str] | None = None
) -> None:
    """Write a tar stream: directory contents (paths=None) or selected entries."""
    with tarfile.open(fileobj=stream, mode="w|") as tar:
        if paths is None:
            for child in sorted(root.iterdir()):
                _add_tar_path(tar, child, child.name)
            return
        for relative in paths:
            _add_tar_path(tar, root / relative, relative)


def upload_tree(
    executor: Executor,
    host: Host,
    probe_path: str,
    dest: str,
    root: Path,
    patterns: list[str],
) -> tuple[int, str]:
    process = executor.spawn(host, [probe_path, "put", "--dest", dest])
    if process.stdin is None:
        raise ExecError("failed to open stdin for probe put")
    try:
        count = write_tar(process.stdin, root, patterns)
        process.close_stdin()
    except (BrokenPipeError, OSError) as exc:
        process.terminate()
        raise ExecError(f"upload to {dest} failed: {exc}") from exc
    stdout = process.stdout.read() if process.stdout is not None else b""
    stderr = process.stderr.read() if process.stderr is not None else b""
    code = process.wait()
    if code != 0:
        raise ExecError(
            f"probe put on {dest} failed ({code}): "
            f"{stderr.decode('utf-8', 'replace').strip()}"
        )
    return count, stdout.decode("utf-8", errors="replace").strip()


def safe_extract(stream: IO[bytes], destination: Path) -> int:
    destination = destination.resolve()
    count = 0
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for member in tar:
            target = (destination / member.name).resolve()
            if destination != target and destination not in target.parents:
                raise ExecError(f"unsafe tar entry rejected: {member.name}")
            if member.issym() or member.islnk():
                raise ExecError(f"links are not extracted locally: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    continue
                target.write_bytes(source.read())
            count += 1
    return count


def copy_tree_local(root: Path, dest: Path, patterns: list[str]) -> int:
    """Copy the workspace to a local directory without deleting extra files."""
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    root_resolved = root.resolve()
    skip: str | None = None
    try:
        skip = dest_resolved.relative_to(root_resolved).as_posix()
    except ValueError:
        pass
    count = 0
    for path, rel in _iter_entries(root, patterns):
        if skip is not None and (rel == skip or rel.startswith(skip + "/")):
            continue
        target = dest / rel
        if path.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            target.symlink_to(path.readlink())
        elif path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        count += 1
    return count


def copy_artifact_local(source: str, is_dir: bool, deploy_workdir: str) -> None:
    src = Path(source)
    dest = Path(deploy_workdir)
    dest.mkdir(parents=True, exist_ok=True)
    if is_dir:
        if src.resolve() == dest.resolve():
            return
        shutil.copytree(src, dest, dirs_exist_ok=True, symlinks=True)
        return
    target = dest / src.name
    if target.resolve() == src.resolve():
        return
    shutil.copy2(src, target)


@dataclass
class RemoteScriptOutcome:
    code: int
    stdout: str
    stderr: str
    result: str | None


def push_stream_to_target(
    executor: Executor,
    host: Host,
    workdir: str,
    chunks: Iterable[bytes],
) -> None:
    remote = (
        f"mkdir -p {shlex.quote(workdir)} && "
        f"tar -xf - -C {shlex.quote(workdir)}"
    )
    process = executor.spawn(host, ["sh", "-c", remote])
    if process.stdin is None:
        raise ExecError("failed to open target stdin")
    try:
        for chunk in chunks:
            process.send(chunk)
        process.close_stdin()
    except (BrokenPipeError, OSError) as exc:
        process.terminate()
        raise ExecError(f"failed to push artifacts: {exc}") from exc
    stderr = process.stderr.read() if process.stderr else b""
    code = process.wait()
    if code != 0:
        raise ExecError(
            f"artifact extraction failed ({code}): "
            f"{stderr.decode('utf-8', 'replace').strip()}"
        )


def run_target_script(
    executor: Executor,
    host: Host,
    script: bytes,
    cwd: str,
    env: dict[str, str],
    *,
    timeout: float | None = None,
) -> RemoteScriptOutcome:
    script_path = f"/tmp/ferry-script-{uuid.uuid4().hex}.sh"
    result_path = f"/tmp/ferry-result-{uuid.uuid4().hex}"
    script_env = dict(env)
    script_env["FERRY_RESULT_FILE"] = result_path
    env_prefix = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in sorted(script_env.items())
    )
    remote = (
        f"cat > {shlex.quote(script_path)} && chmod 700 {shlex.quote(script_path)} && "
        f"mkdir -p {shlex.quote(cwd)} && cd {shlex.quote(cwd)} && "
        f"{env_prefix} sh {shlex.quote(script_path)}; rc=$?; "
        f"rm -f {shlex.quote(script_path)}; exit $rc"
    )
    completed = executor.run(host, ["sh", "-c", remote], stdin=script, timeout=timeout)

    quoted_result = shlex.quote(result_path)
    fetch = (
        f"if [ -f {quoted_result} ]; then printf 'X'; cat {quoted_result}; "
        f"rm -f {quoted_result}; else printf 'M'; fi"
    )
    fetched = executor.run(host, ["sh", "-c", fetch], timeout=15)
    result: str | None = None
    if fetched.returncode == 0 and fetched.stdout.startswith(b"X"):
        payload = fetched.stdout[1:]
        if not payload:
            result = "(empty)"
        else:
            text = payload.split(b"\n", 1)[0].decode("utf-8", errors="replace")
            result = text[:512] + (" (truncated)" if len(text) > 512 else "")
    return RemoteScriptOutcome(
        code=completed.returncode,
        stdout=completed.stdout_text,
        stderr=completed.stderr_text,
        result=result,
    )


def artifact_parts(source: str, is_dir: bool) -> tuple[str, str]:
    if is_dir:
        return source, "."
    return posixpath.dirname(source), posixpath.basename(source)


def ssh_command(
    node: NodeSpec,
    remote: str,
    *,
    key: str | None = None,
) -> str:
    args = [
        "ssh",
        *node.ssh_options(),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if key:
        args.extend(["-i", key])
    args.append(node.destination)
    args.append(remote)
    return " ".join(shlex.quote(item) for item in args)


def artifact_transfer_command(
    source: str,
    is_dir: bool,
    target: NodeSpec,
    workdir: str,
    *,
    key: str | None = None,
) -> str:
    root, name = artifact_parts(source, is_dir)
    remote = (
        f"mkdir -p {shlex.quote(workdir)} && "
        f"tar -xf - -C {shlex.quote(workdir)}"
    )
    ssh = ssh_command(target, remote, key=key)
    return f"tar -C {shlex.quote(root)} -cf - {shlex.quote(name)} | {ssh}"


def artifact_copy_command(source: str, is_dir: bool, workdir: str) -> str:
    if is_dir:
        return (
            f"mkdir -p {shlex.quote(workdir)} && "
            f"cp -a {shlex.quote(source)}/. {shlex.quote(workdir)}/"
        )
    return (
        f"mkdir -p {shlex.quote(workdir)} && "
        f"cp -a {shlex.quote(source)} {shlex.quote(workdir)}/"
    )


def classify_artifacts_command(workdir: str, entries: list[str]) -> str:
    quoted_workdir = shlex.quote(workdir)
    lines = [f"cd {quoted_workdir} 2>/dev/null || true"]
    for entry in entries:
        quoted = shlex.quote(entry)
        lines.append(
            f'if [ -d {quoted} ]; then echo "dir:{entry}"; '
            f'elif [ -f {quoted} ]; then echo "file:{entry}"; '
            f'else echo "missing:{entry}"; fi'
        )
    return "; ".join(lines)
