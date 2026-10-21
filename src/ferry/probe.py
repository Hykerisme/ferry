from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .executors import (
    ExecError,
    Executor,
    Host,
    Process,
    explain_ssh_failure,
    remote_mkdir_and_upload,
)
from .models import ExecEvent, ExecRequest, NodeInfo, ProbeVersion
from .paths import bin_dir

PROBE_VERSION = __version__


class ProbeError(Exception):
    pass


def probe_source_dir() -> Path:
    override = os.environ.get("FERRY_PROBE_SRC")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "probe"


@dataclass(frozen=True)
class Platform:
    id: str
    goos: str
    goarch: str
    goarm: str | None
    machines: tuple[str, ...]
    default_bundle: bool


def bundled_probe_dir() -> Path:
    return Path(__file__).resolve().parent / "_probes"


def platforms_path() -> Path:
    return bundled_probe_dir() / "platforms.json"


def load_platforms() -> list[Platform]:
    path = platforms_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot read platform matrix {path}: {exc}") from exc
    platforms: list[Platform] = []
    for item in payload.get("platforms", []):
        platforms.append(
            Platform(
                id=str(item["id"]),
                goos=str(item.get("goos", "linux")),
                goarch=str(item["goarch"]),
                goarm=str(item["goarm"]) if item.get("goarm") else None,
                machines=tuple(str(machine).lower() for machine in item.get("machines", [])),
                default_bundle=bool(item.get("default_bundle", False)),
            )
        )
    return platforms


def platform_by_id(platform_id: str) -> Platform | None:
    for platform in load_platforms():
        if platform.id == platform_id:
            return platform
    return None


def platform_for_machine(machine: str) -> Platform | None:
    normalized = machine.lower()
    for platform in load_platforms():
        if normalized in platform.machines:
            return platform
    return None


def supported_machines() -> str:
    return ", ".join(
        f"{machine} ({platform.id})"
        for platform in load_platforms()
        for machine in platform.machines
    )


def bundled_probe(platform_id: str) -> tuple[Path, str] | None:
    directory = bundled_probe_dir()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get("version") != PROBE_VERSION:
        return None
    digest = manifest.get("hash")
    mapping = manifest.get("platforms") or manifest.get("architectures") or {}
    relative = mapping.get(platform_id)
    if relative is None and platform_id.startswith("linux-"):
        relative = mapping.get(platform_id.removeprefix("linux-"))
    if not digest or not relative:
        return None
    probe = directory / relative
    if not probe.is_file():
        return None
    return probe, str(digest)


def source_hash(source: Path | None = None) -> str:
    source = source or probe_source_dir()
    if not source.is_dir():
        raise ProbeError(f"probe sources not found at {source}")
    digest = hashlib.sha256()
    files = sorted([*source.glob("*.go"), source / "go.mod"])
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def ensure_local_probe(platform_id: str, *, rebuild: bool = False) -> tuple[Path, str]:
    source = probe_source_dir()
    bundled = bundled_probe(platform_id)
    if bundled is not None:
        bundled_path, bundled_hash = bundled
        if not source.is_dir() or bundled_hash == source_hash(source):
            return _install_bundled(bundled_path, bundled_hash, platform_id, rebuild)

    digest = source_hash()
    target = bin_dir() / f"probe-{platform_id}-{digest}"
    if target.is_file() and not rebuild:
        return target, digest
    if not source.is_dir():
        supported = ", ".join(platform.id for platform in load_platforms())
        raise ProbeError(
            f"no bundled probe for {platform_id!r} and probe sources are not "
            f"available; bundled platforms: {supported or 'none'}. "
            "Use a build node whose 'uname -m' is in platforms.json, or install "
            "from a source checkout with Go."
        )
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_probe.py"
    if not script.is_file():
        raise ProbeError(
            f"probe binary missing and build script not found at {script}; "
            "install Ferry from the repository or set FERRY_PROBE_SRC"
        )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--platform",
            platform_id,
            "--out-dir",
            str(bin_dir()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ProbeError(
            f"failed to build probe for {platform_id} (is Go installed?): "
            f"{result.stderr.strip()}"
        )
    if not target.is_file():
        raise ProbeError(f"probe build did not produce {target}")
    return target, digest


def _install_bundled(
    path: Path, digest: str, platform_id: str, rebuild: bool
) -> tuple[Path, str]:
    if digest.encode("ascii") not in path.read_bytes():
        raise ProbeError(
            f"bundled probe {path} does not contain the manifest hash {digest}. "
            "The package was built with a stale probe binary; rebuild it with "
            "'uv run python scripts/build_probe.py --bundle'."
        )
    target = bin_dir() / f"probe-{platform_id}-{digest}"
    bin_dir().mkdir(parents=True, exist_ok=True)
    if (
        rebuild
        or not target.is_file()
        or target.stat().st_size != path.stat().st_size
    ):
        shutil.copyfile(path, target)
        os.chmod(target, 0o755)
    return target, digest


def detect_platform(executor: Executor, host: Host) -> Platform:
    result = executor.run(host, ["uname", "-m"], timeout=15)
    if result.returncode != 0:
        raise ProbeError(explain_ssh_failure(host, result.stderr_text))
    machine = result.stdout_text.strip().lower()
    platform = platform_for_machine(machine)
    if platform is None:
        raise ProbeError(
            f"unsupported build machine architecture {machine or 'unknown'!r} on "
            f"{host}; supported: {supported_machines()}. Add the architecture to "
            "src/ferry/_probes/platforms.json and rebuild the probe."
        )
    return platform


def parse_version(text: str) -> ProbeVersion:
    lines = [line for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            return ProbeVersion.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    raise ProbeError(f"probe did not return a version document: {text.strip()}")


def handshake(executor: Executor, host: Host, remote_path: str) -> ProbeVersion | None:
    result = executor.run(host, [remote_path, "version"], timeout=20)
    if result.returncode != 0:
        return None
    try:
        return parse_version(result.stdout_text)
    except ProbeError:
        return None


@dataclass
class RemoteProbe:
    executor: Executor
    host: Host
    path: str
    version: ProbeVersion
    _process: Process | None = None
    _lock_process: Process | None = None

    def info(self) -> NodeInfo:
        result = self.executor.run(self.host, [self.path, "probe"], timeout=30)
        if result.returncode != 0:
            raise ProbeError(
                f"probe info failed on {self.host}: {result.stderr_text.strip()}"
            )
        try:
            return NodeInfo.model_validate(json.loads(result.stdout_text))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProbeError(f"invalid probe info from {self.host}: {exc}") from exc

    def exec(self, request: ExecRequest) -> Iterator[ExecEvent]:
        payload = request.model_dump_json().encode("utf-8")
        process = self.executor.spawn(self.host, [self.path, "exec"], stdin=payload)
        self._process = process
        if process.stdout is None:
            raise ProbeError("probe process has no stdout")
        try:
            for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    yield ExecEvent.model_validate(json.loads(line))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ProbeError(
                        f"invalid probe event from {self.host}: {line}"
                    ) from exc
        except BaseException:
            process.terminate()
            self._process = None
            raise
        code = process.wait()
        self._process = None
        if code not in (0, 1, 3, 5):
            stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
            raise ProbeError(f"probe exec failed on {self.host} ({code}): {stderr.strip()}")

    def terminate(self) -> None:
        process = self._process
        if process is not None:
            process.terminate()

    def acquire_lock(self, lock_path: str) -> int:
        process = self.executor.spawn(
            self.host, [self.path, "lock", "--path", lock_path]
        )
        if process.stdout is None:
            raise ProbeError("probe lock process has no stdout")
        line = process.stdout.readline()
        try:
            payload = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            process.close_stdin()
            process.wait()
            raise ProbeError("probe lock returned an invalid response") from exc
        if not payload.get("ok"):
            process.close_stdin()
            process.wait()
            raise ProbeError(
                str(payload.get("message") or "another ferry run is active for this target")
            )
        self._lock_process = process
        return int(payload.get("pid") or 0)

    def release_lock(self) -> None:
        process = self._lock_process
        self._lock_process = None
        if process is None:
            return
        process.close_stdin()
        try:
            process.wait(timeout=20)
        except Exception:
            process.terminate()

    def get_stream(self, root: str, paths: list[str] | None = None) -> Iterator[bytes]:
        argv = [self.path, "get", "--root", root]
        if paths:
            argv.extend(["--paths", ",".join(paths)])
        process = self.executor.spawn(self.host, argv)
        if process.stdout is None:
            raise ProbeError("probe process has no stdout")
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            yield chunk
        code = process.wait()
        if code != 0:
            stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
            raise ProbeError(f"probe get failed on {self.host} ({code}): {stderr.strip()}")


def ensure_remote_probe(
    executor: Executor,
    host: Host,
    workdir: str,
    *,
    platform: str | None = None,
    reset: bool = False,
) -> RemoteProbe:
    platform_id = platform or detect_platform(executor, host).id
    local_path, digest = ensure_local_probe(platform_id)
    candidates = [
        f"/tmp/.ferry-probe-{digest}/probe",
        f"{workdir.rstrip('/')}/.ferry/probe-{platform_id}-{digest}",
    ]
    if not reset:
        for candidate in candidates:
            version = handshake(executor, host, candidate)
            if version is not None and version.hash == digest:
                return RemoteProbe(executor=executor, host=host, path=candidate, version=version)

    data = local_path.read_bytes()
    errors: list[str] = []
    for candidate in candidates:
        try:
            remote_mkdir_and_upload(executor, host, candidate, data)
        except ExecError as exc:
            errors.append(str(exc))
            continue
        version = handshake(executor, host, candidate)
        if version is not None and version.hash == digest:
            return RemoteProbe(executor=executor, host=host, path=candidate, version=version)
        if version is None:
            errors.append(f"{candidate} is not executable")
        else:
            errors.append(
                f"{candidate} reports hash {version.hash or '(none)'}, "
                f"expected {digest} for platform {platform_id}"
            )
    raise ProbeError(
        f"failed to install probe on {host} (platform {platform_id}, "
        f"expected build hash {digest}): {'; '.join(errors) or 'unknown error'}. "
        "If the hash differs, the local probe bundle is stale; rebuild it with "
        "'uv run python scripts/build_probe.py --bundle' or use --reset-probe. "
        f"If /tmp is mounted noexec, ensure {workdir} is writable."
    )


def cancel_remote(executor: Executor, host: Host, pids: list[int]) -> None:
    targets = " ".join(str(pid) for pid in pids if pid > 0)
    if not targets:
        return
    command = f"kill -TERM {targets} 2>/dev/null || true"
    executor.run(host, ["sh", "-c", command], timeout=15)


def quote(value: str) -> str:
    return shlex.quote(value)
