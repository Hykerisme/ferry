# Ferry

[ [简体中文](README.md) | [English](README_EN.md) ]

**Ferry是一个轻量级的自动化部署工具，专为个人和工作室级的定制化构建流程。** <br>
服务于以下结构：`[开发机] -> [构建机] -> [目标部署机]`

Ferry把 **「同步代码 → 构建 → 产物路由 → 部署」** 的流程压缩成一次 `ferry run` ，<br>
设置好SSH的HostAlias之后，通过Yaml和Shell脚本来管理整个部署流程。

Ferry将整个流程抽象为以下三个阶段：

1. 把探针和代码送到构建机（支持git管理和持久化）；
2. 按阶段触发用户脚本；
3. 按 SSH 可达性路由产物和触发自动化脚本。

传统的方案之一是 `Justfile + rsync + sshpass`，我们只是将整个流程设计的更人性化，并且**对异构和交叉构建更加友好**。 <br>
**相比于CI方案，从零开始部署的成本也更低**，因为**Ferry并不依托于存储服务器或容器化Runner节点，针对只有busybox的平台也能更好工作。** <br>
就算你使用k230或MaixCAN之类的**基于带busybox的freertos平台，也可以使用Ferry来定制化流程**，可能一定程度上可以绕过低效或设计不友好的原构建流程。

Ferry不强依赖于你已有的Makefile/CMake/GNinja经验，追求**通用化和多样化的组合式构建流程**。 <br>
换言之，Ferry更像是一个构建适配器，通过Yaml配置化你的项目级构建节点和目标节点，通过Shell脚本来自定义你的构建流程。 <br>
**任何会使用Unix命令行的人都可以轻松使用该工具**

此外，由于探针设计轻量且尽可能的无依赖，构建机可以是无状态的虚拟机节点或者OCI容器。

## 快速开始

```bash
ferry init

# 引导：校验节点、推送探针、生成 .ferry.yaml
# 我们以t507作为案例
ferry up \
  --ssh buildvm \
  --target board \
  --name t507 \
  --build-workdir /work/demo \
  --deploy-path /opt/app \
  --build-cmd "cmake -B build && cmake --build build -j" \
  --out build/out \
  --yes

ferry plan t507      # dry-run：预览你的设置
ferry run t507       # 同步 → 构建 → 部署
ferry status
ferry topology
```

`~/.ssh/config` 示例：

```
Host buildvm
  HostName 192.168.1.10
  User dev
  ControlMaster auto
  ControlPersist 10m
```

## 命令总览

[list.md](list.md)有更详细的说明

| 命令                        | 作用                                     | 作用节点                              |
| :-------------------------- | ---------------------------------------- | ------------------------------------- |
| `ferry init`              | 初始化项目，生成配置模板                 | 开发机                                |
| `ferry up`                | 为构建做准备，进行节点预连接，安装探针等 | 所有节点                              |
| `ferry plan <target>`     | 流程预览，检查配置是否有误               | 无                                    |
| `ferry run <target>`      | 进行代码同步、构建、部署。               | 所有节点                              |
| `ferry status [target]`   | 查看上次构建状态                         | 无                                    |
| `ferry topology [target]` | 进行网络拓扑检查                         | 所有节点 `<br>`<br />（纯网络请求） |

## 安装

```bash
uv tool install ferry-tool
# 或：pipx install ferry-tool
ferry --version
```

发布包已内置 linux/amd64 与 linux/arm64 探针，安装后无需 Go。 <br>
开发端依赖于python，应该可以多平台运行。

若需要特殊平台的Ferry，自行构建十分容易（依赖于python环境和uv管理器）：

```bash
git clone <repo> && cd ferry
uv sync
uv run ferry --help
```

## 构建前置条件

- Python 3.10+（用 `uv tool` / `pipx` 安装时自动处理）
- **（可选）从源码运行时**需要 Go 1.22+（用于交叉编译探针；发布 wheel 不需要）
- 开发机可通过 SSH 访问构建机与目标机（密钥或密码方式均可）
- 目标机至少提供 `ssh` + `sh` + `tar`
- Windows 开发机上的 dev 阶段脚本需要 POSIX `sh`（Git for Windows 自带）

> 目前开发机无需安装 rsync：dev→build 同步使用 Python `tarfile` 流 + ssh。

## SSH 认证

默认进行自动密钥配置并配置免密登录。*注意：自动密码缓存机制同机同用户进程理论上可见。*
也可以选择手动登录（每次命令运行时输入一次密码，同一次运行内复用）

### 自动配置密钥式的免密登录（推荐）

```bash
ferry up --ssh buildvm --target board --name t507 ... --ask-pass
```

输入两端密码后，Ferry 会：

1. 在开发机生成 `~/.ferry/keys/id_ed25519`，安装到构建机和目标机；
2. 在构建机生成 `~/.ferry/keys/id_ed25519`，把公钥安装到目标机（供 build→target 直连）；
3. 验证三条边的密钥登录，成功后再继续引导。

之后 `ferry run` 不再需要密码。目标机重新刷机后，重跑一次 `ferry up --ask-pass` 即可。

### 每次运行手动密码登录

```bash
ferry run t507 --ask-pass
```

或用于 CI（不会落盘）：

```bash
export FERRY_BUILD_PASSWORD=...
export FERRY_TARGET_PASSWORD=...
# 两端同密码时可用 FERRY_SSH_PASSWORD
ferry run t507
```

只提供目标机密码时，目标侧脚本和产物会走 `build -> dev -> target` 中转，**目标机密码不会下发到构建机**。~~代价是大产物会经过开发机带宽。~~

## 配置

`.ferry.yaml` 与 `scripts/` 随项目进 git。最小示例：

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

脚本条目的执行位置：

| 写法                                                   | 位置     |
| ------------------------------------------------------ | -------- |
| `scripts/x.sh`（pre_sync）                           | dev      |
| `scripts/x.sh`（post_sync / pre_build / post_build） | build    |
| `scripts/x.sh`（pre_deploy / post_deploy）           | target   |
| `scripts/x.sh@dev` / `@build` / `@target`        | 显式指定 |

脚本运行在项目根（target 侧为部署目录），环境变量显式注入：

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

脚本可以向 `$FERRY_RESULT_FILE` 写一行诊断摘要；Ferry 原样回传。

## 中断语义（如果你感兴趣的话）

- 同一 target 的新 `run` 会在 sync 之前尝试获取构建锁，拿不到就拒绝；不会杀死正在运行的构建，也不会覆盖它正在使用的工作区。 <br>
  （不会自动杀死构建进程，避免构建流程中断）
- `ferry run <target> --cancel` 发送 `SIGTERM`；探针把信号转发给构建进程组（比如docker和cmake会响应该信号并执行清理流程），等待 15 秒后才 `SIGKILL`。
- 构建前写入 marker，正常结束（成功或普通失败）删除。异常中断会保留 marker；下次 `run` 检测到后清理构建目录来做一次全量，随后恢复增量。
- 锁使用 `flock`，进程退出时由内核释放，避免僵尸 PID 文件。

## 本机数据列表

```
~/.ferry/bin/      # 按 hash 缓存的探针二进制
~/.ferry/state/    # 每个 project/target 的最近一次运行状态
~/.ferry/logs/     # 运行日志
~/.ferry/cache/    # 拓扑探测缓存（TTL 5 分钟）
```

## 已知限制

- 目标机必须提供 `tar`；无 tar 的降级链尚未实现。
- tar 同步不会删除构建机上已从源码删除的文件。
- 密码模式下目标侧操作经 dev 中转；需要直连时先用 `up --ask-pass` 安装密钥。
- 不做工具链归档包分发、构建缓存、并发调度、回滚、常驻服务。

## 获得发布Wheel

发布名是 `ferry-tool`，命令仍为 `ferry`。

1. 修改 `pyproject.toml` 的版本号（探针版本从这里读取）。
2. 提交并打 tag：`git tag v0.1.0 && git push origin v0.1.0`。
3. GitHub Actions 会跑测试、交叉编译探针并写入 `src/ferry/_probes`、构建 wheel/sdist、附到 GitHub Release，然后发布到 PyPI。

首次发布前需要在 PyPI 项目设置中配置 Trusted Publishing：

- Owner / Repository：你的 GitHub 仓库
- Workflow：`release.yml`
- Environment：`pypi`

也可以在仓库 secrets 中配置 `PYPI_API_TOKEN` 并改用 token 发布。发布前可本地验证：

```bash
uv run python scripts/build_probe.py --bundle
uv build
uvx twine check dist/*
uv run --isolated --with ./dist/ferry_tool-*-py3-none-any.whl \
  python -c "from ferry.probe import ensure_local_probe; print(ensure_local_probe('amd64'))"
```

## 开发

```bash
uv run ruff check src scripts tests
uv run mypy src
uv run pytest -q

cd probe && go test ./...
uv run python scripts/build_probe.py          # 交叉编译到 ~/.ferry/bin
uv run python scripts/build_probe.py --bundle # 生成随 wheel 分发的探针

# Linux/WSL 探针集成验证（先将 linux 探针放到 /tmp/ferry-it/probe）
bash scripts/wsl_probe_test.sh
bash scripts/wsl_lock_test.sh
```
