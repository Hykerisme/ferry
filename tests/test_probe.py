from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from ferry.probe import (
    PROBE_VERSION,
    ProbeError,
    bundled_probe,
    ensure_local_probe,
    parse_version,
    probe_source_dir,
    source_hash,
)

pytestmark = pytest.mark.skipif(
    not probe_source_dir().is_dir(), reason="probe sources not present"
)


def load_build_script():
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_probe.py"
    spec = importlib.util.spec_from_file_location("build_probe", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_hash_matches_build_script() -> None:
    script = load_build_script()
    assert source_hash() == script.source_hash(script.probe_source_dir())
    assert len(source_hash()) == 12


def test_probe_version_is_shared() -> None:
    script = load_build_script()
    assert PROBE_VERSION == script.PROBE_VERSION


def test_parse_version_ignores_noise() -> None:
    text = (
        "hello\n"
        '{"ok": true, "version": "0.1.0", "hash": "abc", '
        '"os": "linux", "arch": "amd64"}\n'
    )
    version = parse_version(text)
    assert version.hash == "abc"
    assert version.arch == "amd64"


def test_parse_version_rejects_garbage() -> None:
    with pytest.raises(ProbeError):
        parse_version("not json")


def test_bundled_probe_reads_manifest(tmp_path: Path, monkeypatch) -> None:
    bundle = tmp_path / "_probes"
    (bundle / "linux-amd64").mkdir(parents=True)
    (bundle / "linux-amd64" / "probe").write_bytes(b"binary")
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "version": PROBE_VERSION,
                "hash": "abc123",
                "architectures": {"amd64": "linux-amd64/probe"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ferry.probe.bundled_probe_dir", lambda: bundle)
    found = bundled_probe("amd64")
    assert found is not None
    assert found[0].read_bytes() == b"binary"
    assert found[1] == "abc123"
    assert bundled_probe("arm64") is None


def test_bundled_probe_rejects_version_mismatch(tmp_path: Path, monkeypatch) -> None:
    bundle = tmp_path / "_probes"
    (bundle / "linux-amd64").mkdir(parents=True)
    (bundle / "linux-amd64" / "probe").write_bytes(b"binary")
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "version": "0.0.0",
                "hash": "abc123",
                "architectures": {"amd64": "linux-amd64/probe"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ferry.probe.bundled_probe_dir", lambda: bundle)
    assert bundled_probe("amd64") is None


def test_ensure_local_probe_installs_bundle(tmp_path: Path, monkeypatch) -> None:
    bundle = tmp_path / "_probes"
    (bundle / "linux-amd64").mkdir(parents=True)
    (bundle / "linux-amd64" / "probe").write_bytes(b"binary")
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "version": PROBE_VERSION,
                "hash": "abc123",
                "architectures": {"amd64": "linux-amd64/probe"},
            }
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "bin"
    monkeypatch.setattr("ferry.probe.bundled_probe_dir", lambda: bundle)
    monkeypatch.setattr("ferry.probe.probe_source_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr("ferry.probe.bin_dir", lambda: cache)
    path, digest = ensure_local_probe("amd64")
    assert digest == "abc123"
    assert path.read_bytes() == b"binary"
    assert path == cache / "probe-linux-amd64-abc123"
