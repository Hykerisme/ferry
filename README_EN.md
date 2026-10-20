# Ferry

[ [简体中文](README.md) | [English](README_EN.md) ]

**Ferry is a lightweight automated deployment tool designed for customized build workflows at the individual and studio level.**
It serves the following structure: `[dev machine] -> [build machine] -> [target deployment machine]`

Ferry compresses the workflow of **“sync code → build → route artifacts → deploy”** into a single `ferry run`.
After setting up SSH HostAlias, you can manage the entire deployment workflow through YAML and shell scripts.

Ferry abstracts the whole workflow into the following three stages:

1. Deliver the probe and code to the build machine (with git management and persistence support);
2. Trigger user scripts by stage;
3. Route artifacts and trigger automation scripts according to SSH reachability.

One traditional approach is `Justfile + rsync + sshpass`; we simply design the whole workflow to be more user-friendly and **more friendly to heterogeneous and cross builds**.
**Compared with CI solutions, the cost of deploying from scratch is also lower**, because **Ferry does not rely on storage servers or containerized runner nodes, and works better on platforms that only have busybox.**
Even if you use a **FreeRTOS-based platform with busybox** such as k230 or MaixCAN, you can use Ferry to customize the workflow, potentially bypassing an inefficient or unfriendly original build process to some extent.

Ferry does not strongly depend on your existing Makefile/CMake/GNinja experience; it pursues **a universal and diverse compositional build workflow**.
In other words, Ferry is more like a build adapter: it configures your project-level build nodes and target nodes via YAML, and customizes your build workflow via shell scripts.
**Anyone who can use the Unix command line can easily use this tool.**

In addition, because the probe is designed to be lightweight and as dependency-free as possible, the build machine can be a stateless VM node or an OCI container.

## Quick Start

```bash
ferry init

# Bootstrap: validate nodes, push the probe, generate .ferry.yaml
# We use t507 as an example
ferry up \
  --ssh buildvm \
  --target board \
  --name t507 \
  --build-workdir /work/demo \
  --deploy-path /opt/app \
  --build-cmd "cmake -B build && cmake --build build -j" \
  --out build/out \
  --yes

ferry plan t507      # dry-run: preview your settings
ferry run t507       # sync → build → deploy
ferry status
ferry topology
```

`~/.ssh/config` example:

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
| `ferry init`              | Init Project, Generate Config Templete                      | Dev                                                 |
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

The release package bundles linux/amd64 and linux/arm64 probes; no Go is needed after installation.
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
- The dev machine can access the build machine and target machine over SSH (key or password authentication)
- The target machine must provide at least `ssh` + `sh` + `tar`
- dev-stage scripts on a Windows dev machine require POSIX `sh` (bundled with Git for Windows)

> Currently, the dev machine does not need rsync: dev→build sync uses a Python `tarfile` stream + ssh.

## SSH Authentication

By default, automatic key configuration is performed and passwordless login is configured. *Note: the automatic password caching mechanism is theoretically visible to processes of the same user on the same machine.*
You can also choose manual login (each SSH operation will interactively prompt for a password).

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

`.ferry.yaml` and `scripts/` go into git with the project. Minimal example:

```yaml
version: 1
project:
  name: demo
targets:
  t507:
    build:
      node: buildvm
      workdir: /work/demo
    deploy:
      node: board
      path: /opt/app
    toolchain:
      probe: "cat /opt/toolchain/VERSION"
      expect: "v2.4.1"
    build_cmd: "cmake -B build && cmake --build build -j"
    out: build/out
    stages:
      post_sync: [scripts/check-env.sh]
      post_build: [scripts/strip-symbols.sh]
      pre_deploy: [scripts/stop-service.sh]
      post_deploy: [scripts/install-service.sh, scripts/restart.sh]
```

Execution location of script entries:

| Entry                                                 | Location             |
| ----------------------------------------------------- | -------------------- |
| `scripts/x.sh` (pre_sync)                           | dev                  |
| `scripts/x.sh` (post_sync / pre_build / post_build) | build                |
| `scripts/x.sh` (pre_deploy / post_deploy)           | target               |
| `scripts/x.sh@dev` / `@build` / `@target`       | Explicitly specified |

Scripts run at the project root (on the target side, the deployment directory). Environment variables are explicitly injected:

```
XB_TARGET_ARCH=arm64
XB_TARGET_OS=ubuntu
XB_TARGET_OS_VER=18.04
XB_TARGET_INIT=systemd
XB_TARGET_GLIBC=2.27
XB_TARGET_HAS_RSYNC=0
XB_TARGET_HAS_TAR=1
XB_TARGET_HAS_SCP=1
XB_BUILD_TOOLCHAIN_VER=v2.4.1
```

Scripts can write a one-line diagnostic summary to `$FERRY_RESULT_FILE`; Ferry passes it back unchanged.

## Interruption Semantics (if you are interested)

- A new `run` for the same target tries to acquire the build lock before sync and refuses if it cannot; it will not kill a running build or overwrite the workspace it is using.
  (It will not automatically kill build processes, avoiding interruption of the build workflow.)
- `ferry run <target> --cancel` sends `SIGTERM`; the probe forwards the signal to the build process group (for example, docker and cmake will respond to the signal and perform cleanup), waits 15 seconds, and only then sends `SIGKILL`.
- A marker is written before the build and deleted on normal completion (success or ordinary failure). Abnormal interruption keeps the marker; the next `run` detects it and cleans the build directory to perform a full build, then resumes incremental builds.
- The lock uses `flock` and is released by the kernel when the process exits, avoiding stale PID files.

## Local Data List

```
~/.ferry/bin/      # Probe binaries cached by hash
~/.ferry/state/    # Last run status for each project/target
~/.ferry/logs/     # Run logs
~/.ferry/cache/    # Topology probe cache (TTL 5 minutes)
```

## Known Limitations

- The target machine must provide `tar`; the fallback chain for no tar has not been implemented yet.
- tar sync does not delete files on the build machine that have been deleted from the source.
- In password mode, target-side operations are relayed through dev; if a direct connection is needed, first install keys with `up --ask-pass`.
- No toolchain archive distribution, build caching, concurrent scheduling, rollback, or resident services.

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
  python -c "from ferry.probe import ensure_local_probe; print(ensure_local_probe('amd64'))"
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
