#!/usr/bin/env python3
"""Cross-compile the Go probe into the local Ferry cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


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


def build_one(
    source: Path,
    arch: str,
    digest: str,
    out_dir: Path,
    filename: str | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / (filename or f"probe-linux-{arch}-{digest}")
    if output.exists():
        return output
    env = dict(os.environ)
    env.update({"GOOS": "linux", "GOARCH": arch, "CGO_ENABLED": "0"})
    ldflags = f"-s -w -X main.version={PROBE_VERSION} -X main.buildHash={digest}"
    command = [
        "go",
        "build",
        "-trimpath",
        "-buildvcs=false",
        "-ldflags",
        ldflags,
        "-o",
        str(output),
        ".",
    ]
    subprocess.run(command, cwd=source, env=env, check=True)
    return output


def write_manifest(digest: str, architectures: list[str]) -> Path:
    directory = bundle_dir()
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": PROBE_VERSION,
        "hash": digest,
        "architectures": {
            arch: f"linux-{arch}/probe" for arch in sorted(architectures)
        },
    }
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arch",
        action="append",
        choices=["amd64", "arm64"],
        help="target architecture (repeatable, default: amd64 and arm64)",
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

    architectures = args.arch or ["amd64", "arm64"]
    if args.out_dir is not None:
        out_dir = args.out_dir
    elif args.bundle:
        out_dir = bundle_dir()
    else:
        out_dir = cache_dir()

    for arch in architectures:
        if args.bundle:
            arch_dir = out_dir / f"linux-{arch}"
            path = build_one(source, arch, digest, arch_dir, filename="probe")
            os.chmod(path, 0o755)
        else:
            path = build_one(source, arch, digest, out_dir)
        print(f"{arch}\t{path}")
    if args.bundle:
        manifest = write_manifest(digest, architectures)
        print(f"manifest\t{manifest}")
    print(f"hash\t{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
