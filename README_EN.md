# Ferry

[ [简体中文](README.md) | [English](README_EN.md) ]

**Ferry is a lightweight automated deployment tool, designed for customized build workflows at the individual and studio level.** <br>
It serves the following structure: `[dev node] -> [build node] -> [target deployment node]`

Ferry compresses the workflow of **"sync code → build → artifact routing → deploy"** into a single `ferry run`. <br>
After setting up SSH HostAlias, you can manage the entire deployment workflow through YAML and shell scripts.

Ferry abstracts the whole workflow into the following three stages:

1. Deliver the probe and code to the build machine (with git management and persistence support);
2. Trigger user scripts by stage;
3. Route artifacts and trigger automation scripts according to SSH reachability.

One traditional approach is `Justfile + rsync + sshpass`; we simply design the whole workflow to be more user-friendly and **more friendly to heterogeneous and cross builds**. <br>
**Compared with CI solutions, the cost of deploying from scratch is also lower**, because **Ferry does not rely on storage servers or containerized runner nodes, and works better on platforms that only have busybox.** <br>
Even if you use a **minimal busybox platform** such as k230 or MaixCAN, you can use Ferry to customize the workflow, potentially bypassing an inefficient or unfriendly original build process to some extent.

Ferry does not strongly depend on your existing Makefile/CMake/GNinja experience; it pursues **a universal and diverse compositional build workflow**. <br>
In other words, Ferry is more like a build adapter: it configures your project-level build nodes and target nodes via YAML, and customizes your build workflow via shell scripts. <br>
**Anyone who can use the Unix command line can easily use this tool.**

In addition, because the probe is designed to be lightweight and as dependency-free as possible, the build machine can be a stateless VM node or an OCI container.

## Quick Start

```bash
# Initialize a ferry project
ferry init
# Or initialize a local standalone project
# ferry init --local

## Enter .ferry.yaml to edit node configuration

ferry plan t507      # dry-run: preview your settings
ferry run t507       # sync → build → deploy
ferry status # View the last build status
ferry topology # Quickly check reachability of the three points
```

## What is .ferry.yaml?
**`.ferry.yaml` is the file Ferry uses to identify a Ferry project configuration.** <br>
If you have used other CI tools or configuration-based container platforms, you should be familiar with such files.<br>
In fact, during design we heavily referenced the CI/CD tool configurations of GitHub and GitLab, and abstracted the most core and easy-to-understand parts so that you can **get started quickly**.

Using Ferry through such a file has two benefits:
1. **Configuration as build**: If your project has multiple developers, `.ferry.yaml` can **maintain a similar build experience for each developer** and make the build workflow explicit.<br>
For individual developers or studios with cloud build/heterogeneous build needs, you **do not need** to configure a complex distributed development platform or CI tool to **quickly formalize and standardize the workflow**.
2. **Configuration as project**: We have **carefully designed** the configuration items of the file so that it can not only **quickly adapt to any project development experience** and **reduce onboarding time**,<br>
but also serve as a *quick build workflow description* for your project, so that anyone new can start the build with one click without learning a platform-specific build method.
3. **Configuration as preset**: Being small does not mean being simplistic. Small teams often face cross-platform development issues more frequently, and **we support accommodating multiple presets in one project**.<br>
Maybe today you need to deploy to your own computer for testing, tomorrow to a Raspberry Pi, and the day after tomorrow to a Lichee Pi... **No problem, you can have many workflows.**

### I hate interactive configuration. Please tell me how to write .ferry.yaml.
For the complete Chinese template, see [Chinese configuration file template](template_zh.ferry.yaml).
<details>
<summary>[Click to view] I really like YAML that can be written on one page. I wonder if you feel the same.</summary>

```yaml
version: 1
targets:
  give_it_a_nice_name:  # Target machine nickname, does not affect the build workflow
    sync:
      ignore:
        - .git
        - node_modules
    # ---------------- Build node ----------------
    build:
      node: buildnode # (configure HostAlias in ~/.ssh/config)
      # Inline HostAlias configuration is supported; see the template. Note that in inline mode, .ferry.yaml is not synced.
      workdir: "/workspace" # << code will be sent here
      out: "./build/out" # >> expected build artifact location (will be recursively copied to the deploy machine; use an array to filter what is transferred)

      # Execution flow: before -> sync -> run -> after
      before_build: "echo prepare to build" # optional
      run_build: ["./scripts/build.sh"]
      after_build: "echo build completed" # optional

    # ---------------- Target node ----------------
    deploy:
      node: myboard
      workdir: /opt/app

      # Execution flow: before -> sync -> run -> after
      before_deploy: "echo prepare to deploy" # optional
      run_deploy: "echo deploying" # required
      after_deploy: "echo deploy completed" # optional
```

</details>


### I want fully local builds. Ferry doesn't seem suitable for me.
**But wait! (in a dramatically translated tone)**
Congratulations, Ferry also supports local builds.<br>
In fact, Ferry's nodes are not limited to SSH targets. We provide two pronouns to express **node reuse**:
- dev.node represents your dev machine node
- build.node represents your build machine node

In other words, you can absolutely do:<br>
`dev.node -> dev.node -> dev.node` *(fully local build, and without going through OpenSSH)* <br>
`dev.node -> build.node -> build.node` *(execute deploy scripts on the build machine; cloud build, cloud deploy (e.g., server-side deployment))* <br>
`dev.node -> build.node -> dev.node` *(execute deploy scripts on the dev machine; cloud build, local deploy (e.g., flashing a microcontroller))* <br>
... (and so on)

<details>
<summary>[Click to view] It's actually not much different from the above; just change all nodes to node: dev.node</summary>

```yaml
version: 1
targets:
  my_computer: # Does not affect the build workflow; assume we use this preset for local builds
    sync:
      ignore:
        - .git
        - node_modules
    # ---------------- Build node ----------------
    build:
      node: dev.node # (configure HostAlias in ~/.ssh/config)
      # Inline HostAlias configuration is supported; see the template. Note that in inline mode, .ferry.yaml is not synced.
      workdir: "/workspace" # << code will be sent here
      out: "./build/out" # >> expected build artifact location (will be recursively copied to the deploy machine; use an array to filter what is transferred)

      # Execution flow: before -> sync -> run -> after
      before_build: "echo prepare to build" # optional
      run_build: ["./scripts/build.sh"]
      after_build: "echo build completed" # optional

    # ---------------- Target node ----------------
    deploy:
      node: dev.node
      workdir: /tmp

      # Execution flow: before -> sync -> run -> after
      before_deploy: "echo prepare to deploy" # optional
      run_deploy: "pretend you need a command to flash a physical device" # required
      after_deploy: "echo deploy completed" # optional

```

</details>

## Command Overview

[list.md](list.md) has more detailed explanations.

| Command                     | Purpose                                                          | Node                                     |
| :-------------------------- | ---------------------------------------------------------------- | ---------------------------------------- |
| `ferry init`              | Initialize the project and generate a config template (`--local` generates a standalone target) | Dev machine                              |
| `ferry up`                | Prepare for building: pre-connect nodes, install probes, etc.    | All nodes                                |
| `ferry plan <target>`     | Workflow preview; check whether the configuration is correct     | None                                     |
| `ferry run <target>`      | Perform code sync, build, and deploy.                            | All nodes                                |
| `ferry status [target]`   | View the last build status                                       | None                                     |
| `ferry topology [target]` | Perform network topology checks                                  | All nodes <br> <br /> (pure network requests) |

## Installation

```bash
uv tool install ferry-tool
# or: pipx install ferry-tool
ferry --version
```

The release package bundles linux/amd64, linux/arm64, and linux/armv7 probes. <br>
The development side depends on Python and should run on multiple platforms.

If you need Ferry for a special platform, building it yourself is very easy (depends on a Python environment and the uv manager):

```bash
git clone <repo> && cd ferry
uv sync
uv run ferry --help
```

## Prerequisites for Building Ferry

- Python 3.10+ (handled automatically when installing with `uv tool` / `pipx`)
- **(Optional) Go 1.22+ is required when running from source** (for cross-compiling probes; not required for the released wheel)
- Yes, that's it. (We want to make it as lightweight as possible.)

## SSH Authentication

By default, automatic key configuration is performed and passwordless login is configured. *Note: the automatic password caching mechanism is theoretically visible to processes of the same user on the same machine.*
You can also choose manual login (enter the password once per command run and reuse it within the same run).

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

## Security
not decided yet


## Interruption Semantics (if you are interested)

- A new `run` for the same target tries to acquire the build lock before sync and refuses if it cannot; it will not kill a running build or overwrite the workspace it is using. <br>
  (It will not automatically kill build processes, avoiding interruption of the build workflow.)
- `ferry run <target> --cancel` requests the build to stop: remote builds send `SIGTERM`, and the probe forwards the signal to the build process group (for example, docker and cmake will respond to the signal and perform cleanup), waits 15 seconds, and only then sends `SIGKILL`; local runs send `SIGINT` on POSIX and use `taskkill /T` on Windows.
- A marker is written before `run_build` and deleted on normal completion (success or ordinary failure). Abnormal interruption keeps the marker; the next `run` detects it and cleans the first directory segment of `out` to perform a full build. Interruption during the `before_build`/`sync` stage does not write a marker.
- The lock uses `flock` (`msvcrt.locking` on Windows) and is released by the kernel when the process exits, avoiding stale PID files.

## Local Temporary Data List

```
~/.ferry/bin/      # Probe binaries cached by hash
~/.ferry/keys/     # Keys managed by Ferry
~/.ferry/state/    # Last run status for each project/target
~/.ferry/logs/     # Run logs
~/.ferry/cache/    # Topology probe cache (TTL 5 minutes)
```

## Known Limitations

- (Non-local mode only) The target machine must provide `tar` + `sh`;
- In incremental transfer mode, deleted files are not automatically deleted on the remote machine;
- [By design] (node reuse scenarios only) The tool does not prevent `out` directories from overlapping; it only issues a warning;
- In password mode, target-side operations are relayed through dev; if a direct connection is needed, first install keys with `up --ask-pass`.
- [By design] No additional toolchains or features are provided; operations beyond transfer depend on user scripts.


## About Contributing to This Project

We do not prohibit any AI from submitting code, provided that:
1. You take full responsibility for the code submitted by AI and ensure that the submitted content has been manually reviewed;
2. You understand the side effects of your code and can clearly explain why the project needs your patch (for example, a bug fix or a serious missing feature);
3. You follow the project's development philosophy: do not add too many features, do not affect the main workflow, and do not increase the onboarding difficulty.

Submissions for the following purposes may be rejected:
1. Submissions without any explanation;
2. Changes or features that do not meet project needs;
3. Difficult onboarding;
4. Too narrow or too broad applicability, or making the project approach a traditional CI tool;
5. Introducing external dependencies.