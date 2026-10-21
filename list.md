# Ferry 命令速查

> 面向日常使用。完整设计见 `INSTRUCTION.md`，配置模板见 `template_zh.ferry.yaml`。
> 版本：`ferry --version`（当前 0.1.0）

## 一句话流程

```text
ferry init      → 生成项目配置与 .gitignore
ferry up        → 填写节点、校验、安装探针，写 private/public 配置
ferry plan      → 预览这次会做什么
ferry run       → 同步 → 构建 → 传输 → 部署
ferry status    → 看结果
```

---

## 安装与全局参数

```bash
uv tool install ferry-tool     # 或 pipx install ferry-tool
ferry --version
ferry --install-completion     # 安装当前 shell 的补全
ferry --help
```

| 参数 | 作用 |
| --- | --- |
| `--version` | 显示版本 |
| `--install-completion` | 安装 shell 补全 |
| `--show-completion` | 打印补全脚本，不安装 |
| `--help` | 查看帮助（每个子命令也支持） |

发布 wheel 内置 `linux-amd64`、`linux-arm64`、`linux-armv7` 探针，安装后不需要 Go。

---

## 命令总览

| 命令 | 什么时候用 | 是否改远端 |
| --- | --- | --- |
| `ferry init` | 新项目第一次接入 | 否 |
| `ferry up` | 新增/更换构建机或部署机 | 是（探针、可选密钥） |
| `ferry plan <target>` | 跑之前确认步骤和路由 | 只读探测 |
| `ferry run <target>` | 日常同步、构建、部署 | 是 |
| `ferry status [target]` | 查看最近一次运行状态 | 否 |
| `ferry topology [target]` | 检查 SSH 可达性 | 只读探测 |

---

## `ferry init`

在当前目录生成 `.ferry.yaml`，并把 `.private.ferry.yaml`、`.env` 加入 `.gitignore`。

```bash
ferry init
ferry init --force        # 覆盖已有 .ferry.yaml（打印 diff）
```

- 生成的 `.ferry.yaml` 只有 `version/project/targets`，`targets` 为空。
- 节点信息通过 `ferry up` 填写，或手动编辑 `.private.ferry.yaml`。

---

## `ferry up`

填写节点、校验 SSH 与目录、安装探针，然后：

- 写 `.private.ferry.yaml`（真实节点配置，gitignore、不同步）；
- 生成 `.ferry.yaml`（移除 `node` / `transfer_from`，其余内容与注释保留）；
- 其他人拿到没有节点的 `.ferry.yaml` 时，`run/plan` 会中断并提示补节点。

```bash
ferry up
ferry up --ssh buildvm --target board --name t507 --yes
ferry up --build-host 192.168.1.10 --build-user dev --build-port 2222 ...
```

| 参数 | 说明 |
| --- | --- |
| `--ssh <别名>` | 构建机 SSH Host 别名 |
| `--build-host/--build-user/--build-port` | 构建机 inline host alias |
| `--target <别名/引用>` | 部署节点别名，或 `dev.node` / `build.node` |
| `--target-host/--target-user/--target-port` | 部署节点 inline host alias |
| `--name <别名>` | target 名称，默认 `default` |
| `--build-workdir <路径>` | 构建机绝对可写路径（必须 POSIX 绝对路径） |
| `--out <路径>` | 产物路径，相对 `build.workdir`；可重复 |
| `--run-build <命令>` | 构建命令（必填） |
| `--run-deploy <命令>` | 部署命令（必填） |
| `--deploy-workdir <路径>` | 部署节点绝对路径 |
| `--transfer-from <引用>` | `build.node`（直推）或 `dev.node`（经 dev 中转） |
| `--before-build/--after-build/--before-deploy/--after-deploy` | 可选命令 |
| `--yes`, `-y` | 不再确认写配置 |
| `--reset` | 重新上传探针 |
| `--ask-pass` | 输入 SSH 密码并安装 Ferry 管理的密钥 |

省略参数时会交互询问；脚本数组、inline 节点等高级写法可以直接编辑 YAML。

---

## `ferry plan <target>`

Dry-run：打印节点、路由、`.env` 变量（值用 `***`）和完整步骤，不执行同步/构建/部署。

```bash
ferry plan t507
ferry plan t507 --refresh-topology
ferry plan t507 --ask-pass
```

---

## `ferry run <target>`

执行完整链路。失败即停，状态和日志落在开发机。

```bash
ferry run t507
ferry run t507 --ask-pass
ferry run t507 --cancel
```

| 参数 | 说明 |
| --- | --- |
| `--cancel` | 对正在运行的构建发 SIGTERM，15 秒宽限后 SIGKILL |
| `--refresh-topology` | 忽略拓扑缓存 |
| `--reset-probe` | 重新上传探针 |
| `--ask-pass` | 交互输入密码（同一次运行内复用） |

**并发规则**：同一 target 同时只能有一个 run；第二个 run 在同步前就会被拒绝。

**中断规则**：Ctrl-C 或 `--cancel` 会保留构建 marker；下次 run 自动清理一次构建目录并全量重建，之后恢复增量。

---

## `ferry status [target]`

```bash
ferry status
ferry status t507
```

状态文件：`~/.ferry/state/<project>/<target>.json`  
日志目录：`~/.ferry/logs/<project>/`

---

## `ferry topology [target]`

探测三条边并打印路由。

```bash
ferry topology
ferry topology t507 --refresh
```

| 边 | 用途 |
| --- | --- |
| `dev -> build` | 同步代码、执行构建 |
| `build -> deploy` | 产物直推 |
| `dev -> deploy` | `build -> deploy` 不通时的中转 |

路由：

- `build -> target`：构建机直接推部署节点。
- `build -> dev -> target`：构建机到不了部署节点，或使用目标机密码；产物与 deploy 脚本经开发机中转。
- `deploy.node: dev.node`：产物拉回开发机，deploy 阶段在本地执行。
- `deploy.node: build.node`：deploy 阶段在构建机执行，产物就地复制。

---

## 配置 v2

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

### 命令字段

- 单字符串 = shell 命令。
- 字符串数组 = 脚本路径，按顺序执行，脚本在对应节点执行。
- `run_build` / `run_deploy` 必填；`before_*` / `after_*` 可选。
- 所有命令的 cwd 是 `build.workdir` 或 `deploy.workdir`。

### `out`

相对 `build.workdir`：

- 目录 → 递归复制目录内容到 `deploy.workdir`；
- 文件 → 复制文件到 `deploy.workdir`；
- 数组 → 逐项复制并合并。

### `node`

- 字符串：`~/.ssh/config` Host 别名。
- 映射：inline host alias，字段与 Host 块一致（`hostname`、`user`、`port`、`IdentityFile`、`ProxyJump` 等）。
- `build.node` 必须是具体节点，不能写 `dev.node` / `build.node`。
- `deploy.node` 额外支持 `dev.node` 和 `build.node`。

### `transfer_from`

- `build.node`：构建机直接推送。
- `dev.node`：先拉回开发机，再由开发机推送。
- 只选择传输路径，不改变产物来源；产物始终来自 build。
- `deploy.node` 是本地引用时忽略并警告。

### 私有配置与 `.env`

- `.private.ferry.yaml`：本机真实节点配置，始终 gitignore、绝不同步。
- 成功 run 后自动生成 `.ferry.yaml`，移除节点定义；其他内容与注释保留。
- `.env`：项目根存在时自动加载，注入所有阶段命令/脚本。
- `.env` 中的 `FERRY_*` 会被忽略；`FERRY_RESULT_FILE` 由 Ferry 每个脚本单独注入。
- `plan` 不显示 `.env` 的值，只显示 key。

---

## 执行顺序

```text
before_build   (build)
sync           (dev -> build)
run_build      (build)
after_build    (build)

before_deploy  (deploy)
transfer       (build -> deploy, 或经 dev)
run_deploy     (deploy)
after_deploy   (deploy)
```

`before_*` 脚本在执行前即时下发，因此环境检查发生在代码/产物批量传输之前。

---

## 密码与密钥

### 一次性安装密钥（推荐）

```bash
ferry up ... --ask-pass
```

- 开发机生成 `~/.ferry/keys/id_ed25519`，安装到构建机和部署节点；
- 构建机生成 `~/.ferry/keys/id_ed25519`，安装到外部部署节点；
- 验证通过后，后续运行不需要密码；
- 部署节点重刷后，重跑一次。

### 每次运行输入密码

```bash
ferry run t507 --ask-pass
```

或用于 CI（不落盘）：

```bash
FERRY_BUILD_PASSWORD=... FERRY_TARGET_PASSWORD=... ferry run t507
FERRY_SSH_PASSWORD=... ferry run t507     # 两端同密码
```

只给部署节点密码时，deploy 侧操作走 `build -> dev -> deploy`，密码不会下发到构建机。

---

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `FERRY_HOME` | 覆盖本机数据目录，默认 `~/.ferry` |
| `FERRY_PROBE_SRC` | 覆盖探针源码目录（开发用） |
| `FERRY_BUILD_PASSWORD` | 构建机密码 |
| `FERRY_TARGET_PASSWORD` | 部署节点密码 |
| `FERRY_SSH_PASSWORD` | 两端共用密码 |
| `.env` 中的任意变量 | 注入所有阶段命令/脚本 |
| `FERRY_RESULT_FILE` | 每个脚本独立的诊断摘要文件 |

`FERRY_*` 是 Ferry 保留前缀；`.env` 中同名项不会生效。

---

## 本机数据

```text
~/.ferry/bin/     探针缓存（按源码 hash 命名）
~/.ferry/keys/    Ferry 管理的密钥
~/.ferry/state/   每个 project/target 的运行状态
~/.ferry/logs/    运行日志
~/.ferry/cache/   拓扑探测缓存（TTL 5 分钟）
```

---

## 常见问题速查

| 现象 | 处理 |
| --- | --- |
| `missing build.node` / `missing deploy.node` | 运行 `ferry up`，或编辑 `.private.ferry.yaml` |
| `no .ferry.yaml found` | 先 `ferry init` |
| `unknown target` | `ferry status` 看已有 target |
| `another ferry run holds the build lock` | 等它结束，或 `ferry run <target> --cancel` |
| `host key ... changed` | 节点被重建；按提示 `ssh-keygen -R <host>` 后重跑 `up` |
| 修改 Go 探针后远端行为没变 | `ferry run <target> --reset-probe` 或 `ferry up --reset` |
| 探针 hash 不一致 | 重新构建 wheel（`build_probe.py --bundle`），再 `--reset-probe` |
| 密码登录失败 | 确认 OpenSSH ≥ 8.4、`ferry-askpass` 在 PATH；或改用 `up --ask-pass` 装密钥 |
| 构建机到不了部署节点 | `ferry topology` 检查；使用 `transfer_from: dev.node` 可强制中转 |
| 产物没传过去 | 确认 `out` 相对 `build.workdir`，且 `run_build` 已生成它们 |
| `.env` 变量没生效 | 确认文件在项目根、变量名不是 `FERRY_` 前缀 |
| inline 节点连不上 | 检查 `hostname` 以及 `IdentityFile`/`ProxyJump` 等 Host 字段 |
| Windows 本地 deploy 失败 | 本地 deploy 需要 POSIX `sh`；`deploy.workdir` 建议使用 `C:/...` 形式 |
| 远端已删除的文件仍残留 | tar 同步不删除远端文件；手工清理构建 workdir |
