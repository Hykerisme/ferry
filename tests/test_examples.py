from __future__ import annotations

from pathlib import Path

from ferry.config import SCRIPT_STAGES, load_config, resolve_stage_scripts

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "t507-arm64"


def test_example_config_is_valid() -> None:
    loaded = load_config(EXAMPLE)
    target = loaded.config.targets["t507_arm64"]
    assert target.build.node == "buildvm"
    assert target.deploy.path == "/opt/app"
    assert target.out == "build/out"


def test_example_scripts_exist_and_resolve() -> None:
    loaded = load_config(EXAMPLE)
    target = loaded.config.targets["t507_arm64"]
    found = 0
    for stage in SCRIPT_STAGES:
        for script in resolve_stage_scripts(target, stage):
            assert (loaded.root / script.path).is_file(), script.path
            found += 1
    assert found >= 5
