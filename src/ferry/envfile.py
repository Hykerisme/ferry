from __future__ import annotations

import re
from pathlib import Path

from .config import ConfigError

ENV_FILENAME = ".env"
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(root: Path) -> dict[str, str]:
    path = root / ENV_FILENAME
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            raise ConfigError(f"invalid .env line {number}: {raw!r}")
        if not _KEY_RE.match(key):
            raise ConfigError(f"invalid .env variable name on line {number}: {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key.startswith("FERRY_"):
            continue
        values[key] = value
    return values


def masked_env(values: dict[str, str]) -> dict[str, str]:
    return {key: "***" for key in values}
