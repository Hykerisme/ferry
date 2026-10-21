from __future__ import annotations

from pathlib import Path

from ferry.private_config import ensure_gitignore, regenerate_public

PRIVATE = """# top comment
version: 1
project:
  name: demo
targets:
  board:
    build:
      # keep this comment
      node: buildvm
      workdir: /work/demo
      out: ./build/out
      run_build: make
    deploy:
      node: board
      transfer_from: build.node
      workdir: /opt/app
      run_deploy: echo deploy
"""


def test_regenerate_public_strips_nodes_and_keeps_comments(tmp_path: Path) -> None:
    private = tmp_path / ".private.ferry.yaml"
    public = tmp_path / ".ferry.yaml"
    private.write_text(PRIVATE, encoding="utf-8")
    regenerate_public(private, public)
    text = public.read_text(encoding="utf-8")
    assert "# top comment" in text
    assert "keep this comment" in text
    assert "node:" not in text
    assert "transfer_from" not in text
    assert "run_build: make" in text


def test_ensure_gitignore_adds_private_and_env(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")
    added = ensure_gitignore(tmp_path)
    assert set(added) == {".private.ferry.yaml", ".env"}
    text = gitignore.read_text(encoding="utf-8")
    assert ".private.ferry.yaml" in text
    assert ".env" in text
    assert ensure_gitignore(tmp_path) == []
