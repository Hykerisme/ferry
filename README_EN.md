# Ferry

[ [简体中文](README.md) | [English](README_EN.md) ]

**Ferry is a lightweight automated deployment tool designed for customized build workflows at the individual and studio level.** <br>
It serves the following structure: `[dev machine] -> [build machine] -> [target deployment machine]`

Ferry compresses the workflow of **“sync code → build → route artifacts → deploy”** into a single `ferry run`. <br>
After setting up SSH HostAlias, you can manage the entire deployment workflow through YAML and shell scripts.

Ferry abstracts the whole workflow into the following three stages:

1. Deliver the probe and code to the build machine (with git management and persistence support);
2. Trigger user scripts by stage;
3. Route artifacts and trigger automation scripts according to SSH reachability.

One traditional approach is `Justfile + rsync + sshpass`; we simply design the whole workflow to be more user-friendly and **more friendly to heterogeneous and cross builds**. <br>
**Compared with CI solutions, the cost of deploying from scratch is also lower**, because **Ferry does not rely on storage servers or containerized runner nodes, and works better on platforms that only have busybox.** <br>
Even if you use a **FreeRTOS-based platform with busybox** such as k230 or MaixCAN, you can use Ferry to customize the workflow, potentially bypassing an inefficient or unfriendly original build process to some extent.

Ferry does not strongly depend on your existing Makefile/CMake/GNinja experience; it pursues **a universal and diverse compositional build workflow**. <br>
In other words, Ferry is more like a build adapter: it configures your project-level build nodes and target nodes via YAML, and customizes your build workflow via shell scripts. <br>
**Anyone who can use the Unix command line can easily use this tool.**

In addition, because the probe is designed to be lightweight and as dependency-free as possible, the build machine can be a stateless VM node or an OCI container.

## Quick Start

```bash
ferry init

# Bootstrap: validate nodes, push the probe, generate .private.ferry.yaml and .ferry.yaml
# We use t507 as an example
ferry up \
  --ssh buildvm \
  --target board \
  --name t507 \
  --build-workdir /work/demo \
  --deploy-workdir /opt/app \
  --run-build "cmake -B build && cmake --build build -j" \
  --run-deploy "echo deployed" \
  --out ./build/out \
  --yes

ferry plan t507      # dry-run: preview your settings
ferry run t507       # sync → build → deploy
ferry status
ferry topology
```

### Single-machine development (no SSH, no remote builder)

When the dev machine also acts as build and deploy, use `dev.node`:

```bash
ferry init --local     # creates target: local, building and deploying locally
# adjust run_build / out in .ferry.yaml
ferry plan local
ferry run local
```

- `build.workdir: .` builds in place at the project root and skips sync.
- Any other path (for example `./buildroot`) copies the workspace there first.
- `deploy.workdir: ./deploy` is relative to the project root.
- No SSH, probe, or remote Linux is needed; script steps require a POSIX `sh`.

`~/.ssh/config` example (remote nodes only):

```
Host buildvm
  HostName 192.168.1.10
  User dev
  ControlMaster auto
  ControlPersist 10m
```

## Command Overview

See details at [list.md](list.md)

| Command                     | Do What                                                     | Affecting Who                                       |
| :-------------------------- | ----------------------------------------------------------- | --------------------------------------------------- |
| `ferry init`              | Init Project, generate a config template (`--local` for one machine) | Dev                                                 |
| `ferry up`                | Prepare for build, try connect nodes, install/prepare probe | ALL NODE                                            |
| `ferry plan <target>`     | Preview, check if procedure valid                           | No body                                             |
| `ferry run <target>`      | Sync Codes, build Codes, deploy results                    | ALL NODE                                            |
| `ferry status [target]`   | Check the latest Status/log                                 | No Body                                             |
| `ferry topology [target]` | Only Check Network reachability                            | ALL NODE `<br>`<br />（Just Network Interaction） |

## Installation

```bash
uv tool install ferry-tool
# or: pipx install ferry-tool
ferry --version
```

The release package bundles linux/amd64, linux/arm64, and linux/armv7 probes; no Go is needed after installation. <br>
The development side depends on Python and should run on multiple platforms.

If you need Ferry for a special platform, building it yourself is very easy (depends on a Python environment and the uv manager):

```bash
git clone <repo> && cd ferry
uv sync
uv run ferry --help
```

## Build Prerequisites

- Python 3.10+ (handled automatically when installing with `uv tool` / `pipx`)
- **(Optional) Go 1.22+ is required when running from source** (for cross-compiling probes; not required for the released wheel)
- The dev machine can access remote build/deploy nodes over SSH (key or password authentication)
- The remote target machine must provide at least `ssh` + `sh` + `tar`
- All stage scripts run on the build or deploy node; local build/deploy (`dev.node`) requires POSIX `sh` on the local machine (bundled with Git for Windows)

> Currently, the dev machine does not need rsync: dev→build sync uses a Python `tarfile` stream + ssh; local mode copies files directly or builds in place.

## SSH Authentication

By default, automatic key configuration is performed and passwordless login is configured. *Note: the automatic password caching mechanism is theoretically visible to processes of the same user on the same machine.* <br>
You can also choose manual login (you enter the password once per command and it is reused for that run).

### Automatically configure key-based passwordless login (recommended)

```bash
ferry up --ssh buildvm --target board --name t507 ... --ask-pass
```

After you enter the passwords for both ends, Ferry will:

1. Generate `~/.ferry/keys/id_ed25519` on the dev machine and install it on the build machine and target machine;
2. Generate `~/.ferry/keys/id_ed25519` on the build machine and install the public key on the target machine (for direct build→target connections);
3. Verify key-based login on all three edges, and continue bootstrapping only after success.

After that, `ferry run` no longer needs a password. After reflashing the target machine, just rerun `ferry up --ask-pass` once.

### Manual password login for each run

```bash
ferry run t507 --ask-pass
```

Or for CI (not written to disk):

```bash
export FERRY_BUILD_PASSWORD=...
export FERRY_TARGET_PASSWORD=...
# When both ends use the same password, you can use FERRY_SSH_PASSWORD
ferry run t507
```

When only the target machine password is provided, target-side scripts and artifacts are relayed via `build -> dev -> target`, and **the target machine password is not sent down to the build machine**. ~~The cost is that large artifacts will pass through the dev machine's bandwidth.~~

## Configuration

`.ferry.yaml` is the committed public config; real node definitions live in `.private.ferry.yaml` (gitignored and never synced). After a successful run, Ferry regenerates `.ferry.yaml` from the private config, removing concrete aliases/inline nodes while keeping symbolic references such as `dev.node` / `build.node`, and preserving other content and comments. If `.env` exists at the project root, Ferry loads it and injects its variables into all stage commands/scripts.

```yaml
version: 1
project:
  name: demo
targets:
  t507:
    sync:
      ignore: [".git", ".ferry", ".private.ferry.yaml", ".env", ".venv", "__pycache__", "node_modules"]
    build:
      node: buildvm
      workdir: /work/demo
      out: ./build/out
      before_build: [scripts/check-env.sh]
      run_build: "cmake -B build && cmake --build build -j"
      after_build: [scripts/strip-symbols.sh]
    deploy:
      node: board
      transfer_from: build.node
      workdir: /opt/app
      before_deploy: [scripts/stop-service.sh]
      run_deploy: "echo deployed"
      after_deploy: [scripts/install-service.sh, scripts/restart.sh]
```

Rules:

- A single string is a shell command; an array is a list of script paths executed in order.
- `run_build` / `run_deploy` are required; `before_*` / `after_*` are optional.
- Every command runs with `build.workdir` or `deploy.workdir` as cwd; each script runs on its own node.
- `before_*` scripts are delivered separately before the bulk transfer, so environment checks fail early.
- `out` is relative to `build.workdir`: a directory copies its contents recursively, a file copies the file, and an array merges each entry.
- `node` accepts an SSH Host alias or an inline host alias; `build.node` also accepts `dev.node` (local build), and `deploy.node` also accepts `dev.node` / `build.node`.
- Remote node `workdir` values must be absolute POSIX paths; `dev.node` accepts paths relative to the project root (`.` , `./buildroot`, `./deploy`).
- A local `build.workdir` equal to the project root builds in place and skips sync; other paths copy the workspace locally first.
- `transfer_from` is ignored with a warning for local builds; `deploy.node: build.node` becomes a local deploy when the build is local.
- `.env` variables are injected into all stage scripts; `FERRY_*` is reserved and `FERRY_RESULT_FILE` is injected per script.
- Scripts may write one line to `$FERRY_RESULT_FILE`; Ferry passes it back unchanged.

## Interruption Semantics (if you are interested)

- A new `run` for the same target tries to acquire the build lock before sync and refuses if it cannot; it will not kill a running build or overwrite the workspace it is using.
  (It will not automatically kill build processes, avoiding interruption of the build workflow.)
- `ferry run <target> --cancel` requests a stop: remote builds receive `SIGTERM`, the probe forwards it to the build process group (for example, docker and cmake clean up), waits 15 seconds, then sends `SIGKILL`; local runs send `SIGINT` on POSIX and use `taskkill /T` on Windows.
- A marker is written before `run_build` and deleted on normal completion (success or ordinary failure). Abnormal interruption keeps the marker; the next `run` cleans the first component of `out` and performs one full build. Interruptions during `before_build`/`sync` do not write a marker.
- The lock uses `flock` (Windows uses `msvcrt.locking`) and is released by the kernel when the process exits, avoiding stale PID files.

## Local Data List

```
~/.ferry/bin/      # Probe binaries cached by hash
~/.ferry/keys/     # Keys managed by Ferry
~/.ferry/state/    # Last run status for each project/target
~/.ferry/logs/     # Run logs
~/.ferry/cache/    # Topology probe cache (TTL 5 minutes)
```

## Known Limitations

- The target machine must provide `tar` + `sh`; the fallback chain for no tar has not been implemented yet (not needed for all-local targets).
- tar sync and local copies do not delete files that have been deleted from the source.
- When multiple `out` directories are merged, files with the same name are overwritten with a warning.
- In password mode, target-side operations are relayed through dev; if a direct connection is needed, first install keys with `up --ask-pass`.
- Ferry does not probe, check, or distribute toolchains; handle that in `before_build` / `run_build`.
- Local-mode commands and scripts require POSIX `sh` on Windows (Git for Windows works).
- No build caching, concurrent scheduling, rollback, or resident services.

## Getting the Release Wheel

The release name is `ferry-tool`, while the command remains `ferry`.

1. Modify the version number in `pyproject.toml` (the probe version is read from here).
2. Commit and tag: `git tag v0.1.0 && git push origin v0.1.0`.
3. GitHub Actions will run tests, cross-compile probes and write them into `src/ferry/_probes`, build the wheel/sdist, attach them to the GitHub Release, and then publish to PyPI.

Before the first release, configure Trusted Publishing in the PyPI project settings:

- Owner / Repository: your GitHub repository
- Workflow: `release.yml`
- Environment: `pypi`

You can also configure `PYPI_API_TOKEN` in repository secrets and switch to token-based publishing. You can verify locally before publishing:

```bash
uv run python scripts/build_probe.py --bundle
uv build
uvx twine check dist/*
uv run --isolated --with ./dist/ferry_tool-*-py3-none-any.whl \
  python -c "from ferry.probe import ensure_local_probe; print(ensure_local_probe('linux-amd64'))"
```

## Development

```bash
uv run ruff check src scripts tests
uv run mypy src
uv run pytest -q

cd probe && go test ./...
uv run python scripts/build_probe.py          # Cross-compile to ~/.ferry/bin
uv run python scripts/build_probe.py --bundle # Generate probes distributed with the wheel

# Linux/WSL probe integration verification (first put the Linux probe at /tmp/ferry-it/probe)
bash scripts/wsl_probe_test.sh
bash scripts/wsl_lock_test.sh
```
