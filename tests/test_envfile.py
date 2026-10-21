from __future__ import annotations

from pathlib import Path

import pytest

from ferry.config import ConfigError
from ferry.envfile import load_env_file, masked_env


def test_load_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# comment",
                "FOO=bar",
                "export BAZ='hello world'",
                'QUOTED="value"',
                "EMPTY=",
                "FERRY_IGNORED=secret",
            ]
        ),
        encoding="utf-8",
    )
    env = load_env_file(tmp_path)
    assert env == {
        "FOO": "bar",
        "BAZ": "hello world",
        "QUOTED": "value",
        "EMPTY": "",
    }


def test_missing_env_file(tmp_path: Path) -> None:
    assert load_env_file(tmp_path) == {}


def test_invalid_env_line(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("NOT_AN_ASSIGNMENT\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_env_file(tmp_path)


def test_masked_env_hides_values() -> None:
    assert masked_env({"A": "secret", "B": "x"}) == {"A": "***", "B": "***"}
