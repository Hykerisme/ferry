# Ferry

把「同步代码 → 构建 → 产物路由 → 部署」压缩成一次 `ferry run` 的构建-部署引导器。

Ferry 不做构建、不管理工具链，只做三件事：

1. 把探针送到构建机；
2. 按阶段触发用户脚本；
3. 按 SSH 可达性路由数据流。

设计边界与决策见 [INSTRUCTION.md](INSTRUCTION.md)。

## 安装

```bash
uv tool install ferry-tool
# 或：pipx install ferry-tool
ferry --version
```

发布包已内置 linux/amd64 与 linux/arm64 探针，安装后无需 Go。

也可以从源码开发：

```bash
git clone <repo> && cd ferry
uv sync
uv run ferry --help
```

## 前置条件

- Python 3.10+（用 `uv tool` / `pipx` 安装时自动处理）
- **从源码运行时**需要 Go 1.22+（用于交叉编译探针；发布 wheel 不需要）
- 开发机可通过 SSH 免密访问构建机与目标机（推荐写进 `~/.ssh/config`）
- 目标机至少提供 `ssh` + `sh` + `tar`
- Windows 开发机上的 dev 阶段脚本需要 POSIX `sh`（Git for Windows 自带）

> 开发机无需安装 rsync：dev→build 同步使用 Python `tarfile` 流 + ssh。

## 快速开始

```bash
ferry init

# 引导：校验节点、推送探针、生成 .ferry.yaml
ferry up \
  --ssh buildvm \
  --target board \
  --name t507 \
  --build-workdir /work/demo \
  --deploy-path /opt/app \
  --build-cmd "cmake -B build && cmake --build build -j" \
  --out build/out \
  --yes

ferry plan t507      # dry-run：先看完整步骤
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

| 写法 | 位置 |
| --- | --- |
| `scripts/x.sh`（pre_sync） | dev |
| `scripts/x.sh`（post_sync / pre_build / post_build） | build |
| `scripts/x.sh`（pre_deploy / post_deploy） | target |
| `scripts/x.sh@dev` / `@build` / `@target` | 显式指定 |

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

脚本可以向 `$FERRY_RESULT_FILE` 写一行诊断摘要；Ferry 原样回传，不解析、不参与控制流。

## 中断语义

- 同一 target 的新 `run` 会在 sync 之前尝试获取构建锁，拿不到就拒绝；不会杀死正在运行的构建，也不会覆盖它正在使用的工作区。
- `ferry run <target> --cancel` 发送 SIGTERM；探针把信号转发给构建进程组，等待 15 秒后才 SIGKILL。
- 构建前写入 marker，正常结束（成功或普通失败）删除。异常中断会保留 marker；下次 `run` 检测到后清理构建目录并做一次全量，随后恢复增量。
- 锁使用 `flock`，进程退出时由内核释放，不存在僵尸 PID 文件。

## 本机数据

```
~/.ferry/bin/      # 按 hash 缓存的探针二进制
~/.ferry/state/    # 每个 project/target 的最近一次运行状态
~/.ferry/logs/     # 运行日志
~/.ferry/cache/    # 拓扑探测缓存（TTL 5 分钟）
```

## 已知限制（MVP）

- 拓扑固定为 `build -> target` 直连；`build -> dev -> target` 回退尚未实现。
- 目标机必须提供 `tar`；无 tar 的降级链尚未实现。
- tar 同步不会删除构建机上已从源码删除的文件。
- 不做工具链归档包分发、构建缓存、并发调度、回滚、常驻服务。

## 发布

发布名是 `ferry-tool`（PyPI 上 `ferry` 与 `ferry-cli` 已被占用），命令仍为 `ferry`。

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

人工验收清单见 `examples/t507-arm64/README.md`。
