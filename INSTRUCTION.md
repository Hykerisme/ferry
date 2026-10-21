# Ferry 项目总述

> 文档用途：作为编程上下文和设计边界。配置的事实标准是 `template_zh.ferry.yaml` / `template_en.ferry.yaml`。实现与本文档冲突时以本文档为准；本文档未覆盖时遵循"最小实现 + 不做未声明的事"。

---

## 1. 一句话定位

**Ferry 是一个面向"工具链随镜像分发、构建节点生命周期多变"场景的构建-部署引导器。它不做构建，不碰工具链，只做三件事：在需要时把探针送到构建节点、在执行节点上触发用户脚本、按可达性路由数据流。**

竞品不是 GitLab CI，是 `justfile + rsync + ssh + 一个状态脚本` 这个组合。Ferry 要赢的是 TTHW（time to hello world），不是功能数量。

---

## 2. 问题域

典型用户的真实约束：

- 交叉编译工具链以 VM 镜像 / OCI 镜像形式随产品迭代重发布，节点会周期性被替换。
- 构建节点无外网（或外网不可靠），不能 `apt install`、不能 `pip install`、不能 `git pull` 公网。
- 开发机在 NAT 后，构建节点无法主动回连开发机。
- 目标机可能是 arm64 板卡、busybox 环境，只有 ssh + sh。
- 开发机本地有完整开发体验，不需要远程 IDE。
- 构建环境与目标机环境成对绑定；一个 target 描述一条完整链。

Ferry 把 `配置一次 → ferry run → 结束` 作为目标；脚本和配置构成项目的可提交部分。

---

## 3. 非目标（做了就是向 CI 滑坡，禁止）

- ❌ 多目标并发调度、构建队列、任务优先级
- ❌ 构建缓存（ccache、增量产物复用）
- ❌ 工具管理容器/VM 的生命周期（不 `docker run`、不启停 VM）
- ❌ 权限模型、多用户、审计日志
- ❌ Web 面板、HTTP API、常驻中心服务
- ❌ YAML 内的条件、循环、模板插值、变量引用（逻辑必须在脚本里）
- ❌ 回滚机制（失败即停 + 明确错误归属即可）
- ❌ 队列化异步执行（dev 断开连接则任务中断；同一 target 同时只允许一个实例）
- ❌ 探针在 receiver 侧持久化运行状态（执行完即退出）
- ❌ 目标机安装任何常驻 agent
- ❌ 工具链版本探测/检查/分发（由用户自己的 `run_build`/`before_build` 处理）

**判定规则**：当你发现自己在实现"构建缓存""并发队列""权限模型""容器编排""工具链管理"时，立刻停止。

---

## 4. 核心概念

### 4.1 节点与链

```text
dev (运行 Ferry 的机器，隐式存在)
  -> build (同步代码、执行构建)
  -> deploy (接收产物、执行部署)
```

- 一个 target = 一条 `dev → build → deploy` 链。
- `build` 是构建节点；`deploy` 是部署节点。
- 节点写法：
  - 字符串：`~/.ssh/config` 的 Host 别名；
  - inline 映射：与 `~/.ssh/config` Host 块字段一致（`hostname`、`user`、`port`、`IdentityFile`、`ProxyJump` 等）。
- `build.node` 必须是具体节点或 `dev.node`（在开发机本地构建），不能使用 `build.node` 引用。
- `deploy.node` 额外支持 `dev.node`（在开发机本地执行部署）和 `build.node`（在构建机执行部署）。
- 单机模式：`build.node: dev.node` + `deploy.node: dev.node`，不需要 SSH、探针或远端 Linux。
- 本地 workdir 相对项目根解析；远程 workdir 必须是 POSIX 绝对路径。

### 4.2 Target 配置

```yaml
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

**命令字段**：

- 单字符串 = shell 命令；
- 字符串数组 = 脚本路径，按顺序执行；
- `run_build` / `run_deploy` 必填，`before_*` / `after_*` 可选；
- 所有命令的 cwd 是 `build.workdir` 或 `deploy.workdir`。

**`out`**（必填，相对 `build.workdir`）：

- 目录 → 递归复制目录内容到 `deploy.workdir`；
- 文件 → 复制文件；
- 数组 → 逐项复制并合并。

**`sync.ignore`**：默认排除 `.git`、`.ferry`、`.private.ferry.yaml`、`.env`、`.venv`、`__pycache__`、`node_modules`；`.private.ferry.yaml` 与 `.env` 永远不传输，`out` 的第一段也会自动排除。

**本地模式**：

- `build.node: dev.node` 时，`workdir` 可写相对项目根的路径（`.`、`./buildroot`）。
- `workdir` 等于项目根 → 原地构建并跳过 sync；其他路径 → 先用同一套 `sync.ignore` 本地复制工作区。
- 本地 `deploy.node: dev.node` 的 `workdir` 同样可相对项目根。
- 本地构建 + `deploy.node: build.node` 归一化为本地部署，并给出警告。
- 本地构建时 `transfer_from` 无意义，忽略并警告。

配置进 git 管理。`.ferry.yaml` 是可提交的公开配置；真实节点放在 `.private.ferry.yaml`。

### 4.3 探针（Probe）

推送到构建节点的**静态二进制**，职责严格限定：

- `up` 阶段：探测构建节点环境（arch、os、init、glibc、可用传输工具），回传结果。
- `run` 阶段：**哑执行器**——接收扁平执行计划（脚本/命令 + 环境变量 + 超时），顺序执行，流式回传 exit code / stdout / 时长。**不做任何决策**。
- 同时提供 `put`/`get` 用于代码同步与产物拉取。

**探针生命周期 = 一次调用的生命周期**，执行完即退出；唯一例外是构建目录中的崩溃标记（见 4.4）。

**接口契约（Go，五个方法）**：

```go
type Probe interface {
    Probe() NodeInfo
    Exec(plan []Step) <-chan StepResult
    Put(local, remote string) error
    Get(remote, local string) error
    Version() string
}
```

接口不许增加方法；新增节点类型通过同一接口实现。`LocalProbe` 是同一接口的进程内实现，用于 `build.node: dev.node`，复用 exec/lock/marker/get_stream 语义。

### 4.4 阶段与执行顺序

固定顺序，build 和 deploy 使用同一套 before → transfer → run → after：

```text
before_build  (build)
sync          (dev -> build)
run_build     (build)
after_build   (build)

before_deploy (deploy)
transfer      (build -> deploy，或经 dev)
run_deploy    (deploy)
after_deploy  (deploy)
```

执行语义：

- 所有脚本在它所属的节点执行：build 脚本在构建机，deploy 脚本在部署节点。
- `before_*` 脚本在对应的批量传输之前**单独下发并执行**，用于提前检查环境，避免浪费带宽。
- 代码同步只发生在 `sync`；本地原地构建跳过 sync，本地其他 workdir 使用本地复制。
- 产物传输只发生在 `transfer`；本地 build + 本地 deploy 直接复制文件。
- 脚本在独立子 shell 中执行，cwd 固定为对应 workdir；环境变量由 `.env` + Ferry 注入。
- 任一脚本失败 → **fail-fast**，打印"阶段、节点、exit code、最后 20 行日志"，不继续。
- 目标节点永不驻留 agent：脚本内容由探针/SSH 临时写入并执行，执行后删除。

### 4.5 拓扑与传输路由

每次 `run` 前探测三条边的可达性（`ssh -o ConnectTimeout=3 -o BatchMode=yes`）：

```text
边：dev->build、build->deploy、dev->deploy
```

路由决策：

| 情况 | 传输路径 |
| --- | --- |
| build→deploy 可达 | build 直推 |
| build→deploy 不可达，dev→deploy 可达 | build → dev → deploy |
| `transfer_from: build.node` | 强制 build 直推；不可达则报错 |
| `transfer_from: dev.node` | 强制经 dev 中转；不可达则报错 |
| `deploy.node: dev.node` | 产物拉回 dev，部署阶段本地执行 |
| `deploy.node: build.node` | 部署阶段在 build 执行，产物就地复制 |
| `build.node: dev.node` + 本地 deploy | 无 SSH，本地构建并复制产物 |
| `build.node: dev.node` + 外部 deploy | dev 直接推送产物，deploy 脚本经 SSH 执行；只检查 dev→deploy |

- `transfer_from` 只选择**传输路径**，不改变产物来源；产物始终来自 build 的 `run_build` 输出。
- `deploy.node` 是 `dev.node`/`build.node` 时，`transfer_from` 被忽略并警告。
- 本地构建时 `transfer_from` 被忽略并警告。
- 本地部署时，检查 deploy workdir 与项目根/构建目录是否互相包含，命中则警告。
- 探测结果缓存本地（TTL 5 分钟），`topology` 命令可查看。

### 4.6 环境变量

- **不再自动探测或注入 `XB_*`**。
- 项目根存在 `.env` 时，Ferry 自动加载并注入所有阶段命令/脚本。
- `FERRY_` 是保留前缀；`.env` 中同名前缀被忽略。
- `FERRY_RESULT_FILE` 每个脚本单独注入，永远覆盖 `.env`。
- `plan` 只显示 `.env` 的 key，值显示为 `***`。

### 4.7 脚本结果（Script Result）

脚本向编排层回传一行诊断摘要的通道。设计为"只写、只读、不控制流"。

- 工具在脚本执行前注入 `FERRY_RESULT_FILE`（临时文件路径）。
- 脚本以单行文本写入，内容格式由开发者自定；探针原样回传，不做解析。
- 三态：文件不存在 → `unset`；存在但为空 → `(empty)`；非空 → 原样展示。
- 单行上限 512 字节，超出截断并标记；多行只取第一行。
- 结果不参与控制流、不跨阶段；跨阶段传递走文件系统。

### 4.8 认证、密钥与配置隐私

默认使用 SSH 密钥认证。

- `--ask-pass` 或环境变量启用密码认证：
  - `FERRY_BUILD_PASSWORD` / `FERRY_TARGET_PASSWORD`；两端同密码用 `FERRY_SSH_PASSWORD`。
  - 密码只短暂存在于进程环境，通过 `SSH_ASKPASS`（`ferry-askpass`）传递，不写入配置、状态或日志，不依赖 `sshpass`。
- `ferry up --ask-pass` 一次性引导：
  - 开发机生成 `~/.ferry/keys/id_ed25519`，安装到构建机和外部部署节点；
  - 构建机生成 `~/.ferry/keys/id_ed25519`，安装到外部部署节点，供 `build → deploy` 直连；
  - 验证通过后，后续运行使用密钥。
- 只提供部署节点密码时，deploy 侧操作走 `build → dev → deploy`，目标密码不下发到构建机。
- 部署节点重刷后，重跑一次 `ferry up --ask-pass`。

**配置隐私**：

- `.private.ferry.yaml` 保存真实节点定义，始终加入 `.gitignore`，绝不同步到构建机。
- 每次成功 run 后，Ferry 根据 private 配置生成 `.ferry.yaml`：
  - 删除具体 `build.node`/`deploy.node` 别名或 inline 映射；
  - 保留 `dev.node` / `build.node` 符号引用（不含机器信息）与 `transfer_from`；
  - 其他字段、顺序和注释保留（被删节点块的注释可能丢失）。
- 其他人只拿到 `.ferry.yaml` 时，`run/plan` 会中断并提示补节点（写入 `.private.ferry.yaml` 或运行 `ferry up`）。
- `.private.ferry.yaml` 和 `.env` 自动加入 `.gitignore` 与同步忽略列表。

---

## 5. 命令面

| 命令 | 语义 |
| --- | --- |
| `ferry init [-f] [--local]` | 初始化项目：生成 `.ferry.yaml`，把 `.private.ferry.yaml`/`.env` 加入 `.gitignore`；`--local` 生成可直接 run 的单机 target。 |
| `ferry up [...] [--ask-pass]` | 交互/参数化填写 build/deploy 节点，校验可达性与目录可写，安装探针，写 `.private.ferry.yaml` 并生成 `.ferry.yaml`。支持别名、inline host alias、`dev.node`/`build.node`；本地 build 跳过 SSH/探针。 |
| `ferry plan <target>` | dry-run：打印节点、解析后的 workdir、路由、`.env` key 和完整步骤，不执行。 |
| `ferry run <target> [--cancel]` | 探测 → 同步 → before/run/after build → before/transfer/run/after deploy → 状态落 dev。 |
| `ferry status [target]` | 读本地状态，表格输出阶段状态、时长、日志路径。 |
| `ferry topology [target]` | 打印三边探测结果与路由决策。 |

`--ask-pass`、`--refresh-topology`、`--reset-probe` 等选项见 `ferry <cmd> --help`。

不提供单独的 `push` 命令——同步是 `run` 的一部分。

---

## 6. 状态模型

**状态永远收敛到 dev 侧**；build / deploy 不留持久状态。

```jsonc
// ~/.ferry/state/<project>/<target>.json
{
  "target": "t507",
  "commit": "a1b2c3d",
  "dirty": true,
  "topology": "build->target",
  "stages": {
    "before_build": {"state": "ok"},
    "sync":         {"state": "ok", "dur": "1.2s"},
    "run_build":    {"state": "ok", "dur": "42s"},
    "transfer":     {"state": "ok"},
    "run_deploy":   {"state": "running", "since": "..."}
  },
  "log": "~/.ferry/logs/t507-20261018-1530.log"
}
```

- 阶段 `state` 取值：`ok` / `failed` / `running` / `interrupted`。
- `interrupted` 表示进程异常终止；下次 run 检测到构建机残留 marker 后清理构建目录并全量一次。
- 代码同步语义：MVP 只做工作区模式（tar 同步工作树，状态记录 commit + dirty）。

---

## 7. 关键设计决策

1. **推而非拉**：build 机无外网且不知道 dev 地址，dev 主动推。
2. **只有长连接流式**：`run` 同步阻塞，dev 保持连接；断开则任务中断。工具不承诺"关掉终端明天看结果"。
3. **探针只在 `up` 阶段决策，`run` 阶段是哑执行器**：防止养出第二个 CLI。
4. **OCI 不是另一种 Linux 节点，是另一种生命周期模型**：Ferry 只检查在线/可达/有探针/有工作目录，不拉起容器。
5. **YAML 只声明，不写逻辑**：pydantic 严格校验（`extra='forbid'`）；条件/循环放脚本。
6. **环境变量只来自 `.env`**：不再自动探测 `XB_*`；Ferry 只保留 `FERRY_*` 和 `FERRY_RESULT_FILE`。
7. **目标机永不驻留 agent**：脚本临时写入并 `sh` 执行。
8. **探针执行完即退出**：不留持久状态、锁、日志；崩溃标记是唯一例外。
9. **状态单点收敛到 dev**：build/OCI 侧状态随容器消失，状态必须回传并落 dev。
10. **host key 变化必须显式处理**：`up` 使用 `accept-new`；变化时提示 `ssh-keygen -R`。
11. **脚本结果是脚本 → 工具的单行只读通道**：不解析、不参与控制流。
12. **不对构建进程强杀**：并发请求拒绝；远程取消走 SIGTERM + 15s 宽限；本地 POSIX 用 SIGINT、Windows 用 taskkill；marker 只围绕 `run_build`，正常失败也清除，异常中断才保留。
13. **密码只用于认证，不落盘**：不引入 `sshpass`；目标密码不下发到构建机。
14. **节点信息与公开配置分离**：`.private.ferry.yaml` 是源码，`.ferry.yaml` 是生成物；只删除具体别名/inline 节点，保留 `dev.node`/`build.node` 符号引用，避免泄露机器信息。
15. **脚本按节点归属执行**：build 脚本在 build，deploy 脚本在 deploy；`before_*` 提前单独下发，用于环境检查。

---

## 8. 技术选型

| 角色 | 语言 | 理由 |
| --- | --- | --- |
| dev CLI | **Python 3.10+** | 只在自己机器跑；YAML + pydantic 生态最顺 |
| build 探针 | **Go（静态二进制）** | `CGO_ENABLED=0`，任何 Linux 内核可跑；Ubuntu 18 / busybox 通吃 |
| deploy 侧 | **用户写的 POSIX sh** | 目标机可能只有 busybox，绝不要求运行时 |

**探针分发**：

- 默认打包 `linux-amd64`、`linux-arm64`、`linux-armv7`；`armv6`、`386`、`riscv64` 可按需构建。
- 平台矩阵在 `src/ferry/_probes/platforms.json`；运行时用 `uname -m` 选择。
- `scripts/build_probe.py --bundle` 强制重编译并校验二进制内嵌 hash 与 manifest 一致。

**连接优化**：`~/.ssh/config` 配 `ControlMaster auto` + `ControlPersist 10m`，反复调用省握手延迟。

**传输降级**：dev 机可能没有 rsync，同步使用 Python `tarfile` 流 + ssh；目标机只需 `tar` + `sh`。

---

## 9. 项目结构

### 树 1：用户项目仓库

```text
myproj/
├── .ferry.yaml               # 公开配置（可提交，具体节点被移除，保留符号引用）
├── .private.ferry.yaml       # 真实节点配置（gitignore，不同步）
├── .env                      # 可选，自动注入环境变量（gitignore）
├── scripts/                  # 用户脚本
└── src/ ...
```

### 树 2：dev 本地运行时

```text
~/.ferry/
├── bin/                      # 按 hash 缓存的探针
├── keys/                     # Ferry 管理的密钥
├── state/<project>/          # 每个 target 的最近状态
├── logs/<project>/           # 运行日志
└── cache/                    # 拓扑缓存（TTL 5 分钟）
```

### 树 3：Ferry 工具仓库

```text
ferry/
├── src/ferry/
│   ├── cli.py
│   ├── commands/             # init up plan run status topology
│   ├── config.py             # pydantic schema + 发现
│   ├── nodes.py              # NodeSpec、inline SSH、dev/build 引用
│   ├── private_config.py     # private 加载、public 再生成、gitignore
│   ├── envfile.py            # .env 解析与掩码
│   ├── executors.py          # SshExecutor / LocalExecutor / askpass
│   ├── probe.py              # 探针构建/缓存/握手/NDJSON
│   ├── local_probe.py        # dev.node 的进程内探针实现
│   ├── transport.py          # tar、脚本下发、产物传输
│   ├── topology.py           # 三边探测 + 路由
│   ├── pipeline.py           # phase 调度 + 状态
│   ├── state.py
│   └── auth.py / bootstrap.py
├── probe/                    # Go 探针
├── scripts/build_probe.py
├── template_zh.ferry.yaml
├── template_en.ferry.yaml
└── tests/
```

---

## 10. 用户视角的成功指标

- 新仓库从 `git clone` 到第一次成功 deploy，< 10 分钟，不需读文档。
- 换构建机：改 `.private.ferry.yaml` 一个节点定义 / 重跑 `up`，其余零改动。
- 换部署节点：`ferry up --target ...`，拓扑自动重探测，其余零改动。
- 换 OCI 镜像：如果预装探针且工作目录路径不变，`ferry run` 直接跑通。
- 上传项目不泄露节点：`.ferry.yaml` 已移除具体节点（保留无机器信息的符号引用），`.private.ferry.yaml` 不在 git 中。
- 单机项目：`ferry init --local` 后即可 `ferry run local`，不需要 SSH/探针。
- 失败时：错误明确到阶段、节点、exit code、最后 20 行日志。

---

## 11. 零配置与默认值

配置文件只在偏离约定时才需要写：

- `sync.ignore` 有默认列表（含 `.private.ferry.yaml`、`.env`）。
- `before_*`/`after_*` 可省略；`run_build`/`run_deploy`/`out` 必填。
- `.env` 可选；不存在则不注入额外变量。
- `deploy.transfer_from` 可选；默认按可达性自动选择。
- `deploy.node` 可用 `dev.node`/`build.node` 表示本地执行；`build.node: dev.node` 表示本机构建。
- 本地 build/deploy 的 `workdir` 可相对项目根；`build.workdir: .` 跳过 sync。

**好 schema 让用户在需要时表达，坏 schema 强迫用户每次都表达。**

---

## 12. 信任模型与安全

- `ferry up` 在远端执行探针二进制，等于把 dev 的信任扩散到 build 节点。只对可信 SSH 目标使用。
- 用户脚本可执行任意操作；Ferry 不做沙箱、不做回滚。脚本幂等性是用户责任。
- 密码通过 `SSH_ASKPASS` 传递，不落盘；同机同用户进程理论上可见。
- `.private.ferry.yaml` 是节点信息的唯一来源，必须保持 gitignore。
- 探针版本与 dev CLI 通过 hash 握手；不一致则 dev 自动重推，不做协议向后兼容。

---

## 13. 实现优先级

**已完成（v2）**：`init` / `up` / `plan` / `run` / `status` / `topology`；inline 节点与引用；私有/公开配置；`.env`；before/run/after 阶段；产物目录/文件/数组；直推/中转/本地部署；密码与 key 引导；单机本地构建（`build.node: dev.node`）。

**后续**：OCI handler 完整实现；`ferry watch`（明确认账后再做）。

**永远不做**：见第 3 节。

---

## 14. 已知限制

- 目标机必须提供 `tar` + `sh`。
- tar 同步不删除构建机上已从源码删除的文件。
- 多个 `out` 目录合并时同名文件会覆盖，只打印警告。
- 不做工具链版本检查/分发；由 `before_build`/`run_build` 自行处理。
- 不做构建缓存、并发调度、回滚、常驻服务。
- Windows 本地部署需要 POSIX `sh`，路径包含判断为尽力而为。
