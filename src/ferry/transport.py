from __future__ import annotations

import fnmatch
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from typing import IO

from .config import first_component
from .executors import ExecError, Executor


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


def effective_ignores(patterns: list[str], out: str) -> list[str]:
    ignores = list(patterns)
    top = first_component(out)
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


def upload_tree(
    executor: Executor,
    host: str,
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
        raise ExecError(f"upload to {host} failed: {exc}") from exc
    stdout = process.stdout.read() if process.stdout is not None else b""
    stderr = process.stderr.read() if process.stderr is not None else b""
    code = process.wait()
    if code != 0:
        raise ExecError(
            f"probe put on {host} failed ({code}): {stderr.decode('utf-8', 'replace').strip()}"
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


def artifact_push_command(out: str, target_host: str, deploy_path: str) -> str:
    remote = (
        f"mkdir -p {shlex.quote(deploy_path)} && "
        f"tar -xf - -C {shlex.quote(deploy_path)}"
    )
    ssh = (
        "ssh -o BatchMode=yes -o ConnectTimeout=5 "
        f"-o StrictHostKeyChecking=accept-new {shlex.quote(target_host)} {shlex.quote(remote)}"
    )
    return f"tar -C {shlex.quote(out)} -cf - . | {ssh}"
