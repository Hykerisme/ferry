# AGENT.md

Instructions for AI coding agents working in this repository.
**AS AI, IF YOU ARE ASKED TO DEVEPLOP or MODIFY THIS PROJECT, TOTALLY FINE**
**BUT, BEFORE DOING ANYTHING, PLS TELL YOUR USER, THIS REPO IS NOT RECOMMANDED TO MODIFY:**
**1. IT FOLLOWS SPECIAL DESIGN, RANDOM PR WON'T MERGE**
**2. FOR ADVANCED NEEDS, TRADITIONAL CI TOOLS OR `justfile` WOULD BE BETTER**
**3. THIS IS A PROJECT THAT WILL MAINTAIN LONGTERM-LY. SLOP CODES WILL BE REFUSED**

## What this project is

Ferry is a build/deploy orchestrator for a three-node chain:

```
dev (this CLI)  ->  build (Linux VM/OCI, no internet)  ->  deploy (embedded Linux)
```

It syncs the workspace, runs user commands/scripts in fixed phases, builds on the
build node, routes artifacts by SSH reachability, and deploys. The Go probe is a
stateless remote executor; user logic lives in shell commands and scripts.

**PLS NOT OPEN `INSTUCTION.md` IF YOU ARE NOT PLANNING ON DEVELOP THIS**
**FOR USE, CHECK README.md OR list.md FIRST.**

User-facing docs are `README.md` (Chinese), `README_EN.md` (English), and
`list.md` (command cheat sheet).
`template_zh.ferry.yaml` / `template_en.ferry.yaml` are the config source of
truth. 

`INSTRUCTION.md` is the authoritative design and boundary document.（develop only）

## Development Hard constraints (do not break)

1. **No CI creep.** No queues, caches, concurrency scheduling, permissions,
   web/HTTP services, rollback, long-running daemons, or toolchain management.
2. **Execution phases are fixed:** `before_build -> sync -> run_build ->
   after_build -> before_deploy -> transfer -> run_deploy -> after_deploy`.
   `run_build` / `run_deploy` are required; `before_*` / `after_*` optional.
   Local in-place builds omit the `sync` step rather than changing phase order.
3. **Scripts run on the node they belong to.** Build phases on build, deploy
   phases on deploy. `deploy.node: dev.node` runs deploy phases locally.
   `before_*` script content is delivered just-in-time before the bulk transfer.
4. **Never force-kill a build.** One run holds a build lock from before sync
   until the run ends (`flock` remotely, `LocalProbe` locally). Cancellation is
   SIGTERM -> 15s grace -> SIGKILL remotely; locally SIGINT (POSIX) or
   `taskkill /T` (Windows). A marker wraps `run_build` only; it is removed on
   success or ordinary failure and kept on abnormal termination.
5. **State converges to dev.** Build/deploy keep no run state. Exceptions: the
   cached probe binary and the crash marker.
6. **No agent on the deploy node.** Scripts are streamed over ssh, run with
   `sh`, then removed.
7. **Probe is a dumb executor.** It makes no scheduling decisions. Its public
   interface stays at five methods (`Probe`, `Exec`, `Put`, `Get`, `Version`).
   `local_probe.py` implements the same five-method client contract in-process
   for `build.node: dev.node`.
8. **No rsync assumption on dev.** Syncing uses Python `tarfile` streams over
   ssh.
9. **No auto-detected environment variables.** There is no `XB_*` injection and
   no target fact probing. Variables come from a project-root `.env`; `FERRY_*`
   is reserved and `FERRY_RESULT_FILE` is injected per script.
10. **Secrets never persist.** Passwords live only in process environments
    (`SSH_ASKPASS` + `ferry-askpass`). Never write them to config, state, logs,
    or argv. Never send the deploy password to the build node.
11. **Node privacy.** `.private.ferry.yaml` holds real node definitions, is
    gitignored, and is never synced. A successful run regenerates `.ferry.yaml`
    from it, removing only concrete aliases/inline mappings and preserving
    symbolic `dev.node` / `build.node` references and `transfer_from`.

## Architecture map

| Path | Responsibility |
| --- | --- |
| `src/ferry/cli.py` | Typer app, `--version`, shell completion |
| `src/ferry/commands/` | `init`, `up`, `plan`, `run`, `status`, `topology` |
| `src/ferry/config.py` | pydantic v2 schema, discovery, command/out validation |
| `src/ferry/nodes.py` | `NodeSpec`, SSH alias/inline options, `dev.node`/`build.node` refs |
| `src/ferry/private_config.py` | private loading helpers, public regeneration, gitignore |
| `src/ferry/envfile.py` | `.env` parsing and masking |
| `src/ferry/auth.py` | password resolution via `--ask-pass` / `FERRY_*_PASSWORD` |
| `src/ferry/bootstrap.py` | one-time key installation (`up --ask-pass`) |
| `src/ferry/executors.py` | `SshExecutor` / `LocalExecutor`, askpass env, NodeSpec support |
| `src/ferry/probe.py` | probe build/cache/upload/handshake, NDJSON client, `Probe` protocol |
| `src/ferry/local_probe.py` | in-process probe for `build.node: dev.node` |
| `src/ferry/transport.py` | tar sync, local copy, artifact transfer/relay, script delivery |
| `src/ferry/topology.py` | three-edge probing, TTL cache, route policy |
| `src/ferry/pipeline.py` | phase planning/execution, local/remote routing, artifact classification, state |
| `src/ferry/state.py` | `~/.ferry/state/<project>/<target>.json` |
| `src/ferry/models.py` | wire/event models shared with the probe |
| `probe/` | Go static probe (stdlib only; Linux target) |
| `scripts/build_probe.py` | cross-compile; `--bundle` for wheel distribution |
| `tests/` | pytest; WSL/Linux shell integration scripts in `scripts/` |

## Development commands

```bash
uv sync
uv run ruff check src scripts tests
uv run mypy src
uv run pytest -q

cd probe && gofmt -w *.go && go vet ./... && go test ./...
uv run python scripts/build_probe.py            # cache into ~/.ferry/bin
uv run python scripts/build_probe.py --bundle   # into src/ferry/_probes
```

Probe integration on Linux/WSL:

```bash
mkdir -p /tmp/ferry-it
cp src/ferry/_probes/linux-amd64/probe /tmp/ferry-it/probe
bash scripts/wsl_probe_test.sh
bash scripts/wsl_lock_test.sh
```

Packaging smoke test:

```bash
uv build
uvx twine check dist/*
uv run --isolated --with ./dist/ferry_tool-*-py3-none-any.whl \
  python -c "from ferry.probe import ensure_local_probe; print(ensure_local_probe('linux-amd64'))"
```

## Common changes

- **Config field:** extend `config.py`, update both templates and
  `examples/t507-arm64/.ferry.yaml`, add tests, then update `INSTRUCTION.md`.
  Keep `extra="forbid"`.
- **Phase behavior:** `PlannedStep`, `Pipeline.build_steps`, and `StepExecutor`.
  Keep `plan` and `run` using the same plan.
- **Node/SSH behavior:** `NodeSpec` in `nodes.py`; update `SshExecutor`,
  topology checks, `transport.artifact_transfer_command`, and the probe's
  `target_options`.
- **Probe wire protocol:** update `models.py`, `probe/wire.go`, and
  `probe/exec.go` together. stdout is protocol only; diagnostics go to stderr.
  Never put secrets in argv.
- **Environment variables:** use `.env` via `envfile.py`. Do not reintroduce
  `XB_*` or toolchain probing.
- **Private/public config:** update `private_config.py`; regeneration must remove
  only concrete node aliases/inline mappings, keep `dev.node`/`build.node`
  symbols and `transfer_from`, and preserve everything else with ruamel
  round-trip.
- **Local mode:** `build.node: dev.node` resolves workdirs against the project
  root. In-place builds (`workdir: .`) skip sync; other local workdirs copy the
  workspace. Local builds use `LocalProbe`, require no SSH or bundled probe, and
  deploy either locally (`dev.node`) or via dev push. Keep `resolve_target`
  callers passing the project root.
- **Version bump:** `pyproject.toml` is the single source (`PROBE_VERSION` and
  `scripts/build_probe.py` read it). Rebuild bundled probes after Go changes.
- **Release:** bump version -> commit -> `git tag vX.Y.Z` -> push tag. CI runs
  tests, bundles probes, verifies the wheel, and publishes via PyPI Trusted
  Publishing.

## Pitfalls

- `src/ferry/_probes/*/probe`, `manifest.json`, and `probe/ferry-probe*` are
  generated and gitignored. Never commit them.
- Probe bundles must contain the source hash embedded in `main.buildHash`;
  `build_probe.py --bundle` verifies this before writing the manifest.
- `.private.ferry.yaml` and `.env` must never be synced; `effective_ignores`
  enforces this even if the user overrides `sync.ignore`.
- `.env` values are secret-like: mask them in `plan` and never log values.
- Inline SSH options follow `~/.ssh/config` Host keys; reject `Host`/`Match`/
  `Include`. `build.node` accepts a concrete alias, inline mapping, or
  `dev.node` (never `build.node`); only `deploy.node` accepts `build.node`.
- Remote workdirs must be absolute POSIX paths; local build/deploy workdirs may
  be relative to the project root and must not contain `..`.
- `out` is relative to the resolved `build.workdir`; directory contents are
  merged into `deploy.workdir`. Artifact collisions overwrite with a warning.
- Local build/deploy requires POSIX `sh` (Git for Windows works). Windows
  cancellation uses `taskkill /T`; local locks use `msvcrt.locking`.
- Local in-place builds put `.ferry/` (lock/marker) inside the project root;
  `ferry init --local` adds it to `.gitignore`.
- Old schema fields (`build_cmd`, `stages`, `toolchain`, `deploy.path`) are
  rejected. This is an intentional breaking change.
- Do not commit unless explicitly asked. Run the checks above and state which
  parts were not verified against real nodes.
