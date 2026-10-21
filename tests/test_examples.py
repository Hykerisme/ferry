from __future__ import annotations

from pathlib import Path

from ferry.config import load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "t507-arm64"


def test_example_config_is_valid() -> None:
    loaded = load_config(EXAMPLE)
    target = loaded.config.targets["t507_arm64"]
    assert target.build.node == "buildvm"
    assert target.deploy.node == "target-t507"
    assert target.deploy.workdir == "/opt/app"
    assert target.build.out == "./build/out"


def test_example_scripts_exist() -> None:
    loaded = load_config(EXAMPLE)
    target = loaded.config.targets["t507_arm64"]
    specs = [
        target.build.before_build,
        target.build.after_build,
        target.deploy.before_deploy,
        target.deploy.after_deploy,
    ]
    found = 0
    for spec in specs:
        if not isinstance(spec, list):
            continue
        for path in spec:
            assert (loaded.root / path).is_file(), path
            found += 1
    assert found >= 5
