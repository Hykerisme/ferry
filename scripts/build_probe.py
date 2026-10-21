#!/usr/bin/env python3
"""Cross-compile the Go probe into the local Ferry cache or the Python package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_version() -> str:
    text = (repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib

        return str(tomllib.loads(text)["project"]["version"])
    except ImportError:
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not match:
            raise RuntimeError("cannot determine project version") from None
        return match.group(1)


PROBE_VERSION = project_version()


def probe_source_dir() -> Path:
    return repo_root() / "probe"


def bundle_dir() -> Path:
    return repo_root() / "src" / "ferry" / "_probes"


def platforms_path() -> Path:
    return bundle_dir() / "platforms.json"


def load_platforms() -> list[dict[str, Any]]:
    payload = json.loads(platforms_path().read_text(encoding="utf-8"))
    platforms = payload.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        raise RuntimeError(f"no platforms defined in {platforms_path()}")
    return platforms


def platform_by_id(platform_id: str) -> dict[str, Any]:
    for platform in load_platforms():
        if platform["id"] == platform_id:
            return platform
    available = ", ".join(platform["id"] for platform in load_platforms())
    raise RuntimeError(f"unknown platform {platform_id!r}; available: {available}")


def source_hash(source: Path) -> str:
    digest = hashlib.sha256()
    files = sorted([*source.glob("*.go"), source / "go.mod"])
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def cache_dir() -> Path:
    home = os.environ.get("FERRY_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".ferry"
    return base / "bin"


def verify_embedded_hash(path: Path, digest: str) -> None:
    if digest.encode("ascii") not in path.read_bytes():
        raise RuntimeError(
            f"{path} does not contain the build hash {digest}; "
            "the binary is stale or was built with different sources"
        )


def build_one(
    source: Path,
    platform: dict[str, Any],
    digest: str,
    out_dir: Path,
    *,
    filename: str | None = None,
    force: bool = False,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    platform_id = str(platform["id"])
    output = out_dir / (filename or f"probe-{platform_id}-{digest}")
    if output.exists() and not force:
        try:
            verify_embedded_hash(output, digest)
            return output
        except RuntimeError:
            pass
    env = dict(os.environ)
    env.update(
        {
            "GOOS": str(platform.get("goos", "linux")),
            "GOARCH": str(platform["goarch"]),
            "CGO_ENABLED": "0",
        }
    )
    if platform.get("goarm"):
        env["GOARM"] = str(platform["goarm"])
    ldflags = f"-s -w -X main.version={PROBE_VERSION} -X main.buildHash={digest}"
    temporary = output.with_name(output.name + ".tmp")
    command = [
        "go",
        "build",
        "-trimpath",
        "-buildvcs=false",
        "-ldflags",
        ldflags,
        "-o",
        str(temporary),
        ".",
    ]
    try:
        subprocess.run(command, cwd=source, env=env, check=True)
        verify_embedded_hash(temporary, digest)
        if os.name != "nt":
            temporary.chmod(0o755)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def write_manifest(digest: str, platforms: list[dict[str, Any]]) -> Path:
    directory = bundle_dir()
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": PROBE_VERSION,
        "hash": digest,
        "platforms": {
            str(platform["id"]): f"{platform['id']}/probe"
            for platform in sorted(platforms, key=lambda item: str(item["id"]))
        },
    }
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        action="append",
        help=(
            "platform id from src/ferry/_probes/platforms.json "
            "(repeatable; default: platforms marked default_bundle)"
        ),
    )
    parser.add_argument("--hash-only", action="store_true", help="print the source hash and exit")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="write probes and manifest into the Python package for wheel builds",
    )
    args = parser.parse_args()

    source = probe_source_dir()
    if not source.is_dir():
        print(f"probe sources not found at {source}", file=sys.stderr)
        return 1
    digest = source_hash(source)
    if args.hash_only:
        print(digest)
        return 0

    if args.platform:
        platforms = [platform_by_id(platform_id) for platform_id in args.platform]
    else:
        platforms = [item for item in load_platforms() if item.get("default_bundle")]
    if not platforms:
        print("no platforms selected", file=sys.stderr)
        return 1

    if args.out_dir is not None:
        out_dir = args.out_dir
    elif args.bundle:
        out_dir = bundle_dir()
    else:
        out_dir = cache_dir()

    for platform in platforms:
        platform_id = str(platform["id"])
        if args.bundle:
            arch_dir = out_dir / platform_id
            path = build_one(
                source, platform, digest, arch_dir, filename="probe", force=True
            )
        else:
            path = build_one(source, platform, digest, out_dir)
        print(f"{platform_id}\t{path}")
    if args.bundle:
        manifest = write_manifest(digest, platforms)
        print(f"manifest\t{manifest}")
    print(f"hash\t{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
