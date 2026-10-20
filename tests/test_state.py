from __future__ import annotations

from pathlib import Path

from ferry.paths import state_dir
from ferry.state import RunState, StageState, load_state, save_state, state_path


def test_state_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    state = RunState(
        project="demo",
        target="board",
        run_id="run-1",
        build_node="buildvm",
        stages={"build": StageState(state="ok", dur="1.2s")},
    )
    path = save_state(state)
    assert path == state_path("demo", "board")
    loaded = load_state("demo", "board")
    assert loaded is not None
    assert loaded.run_id == "run-1"
    assert loaded.stages["build"].state == "ok"


def test_missing_state_returns_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    assert load_state("demo", "missing") is None


def test_state_directory_is_created(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FERRY_HOME", str(tmp_path / "home"))
    save_state(RunState(project="p", target="t", run_id="r"))
    assert state_dir().is_dir()
