# Ferry 项目总述

> 文档用途：作为编程上下文喂给大模型。本文档定义 Ferry 的定位、边界、架构与设计纪律。实现时若与本文档冲突，以本文档为准；若本文档未覆盖，遵循"最小实现 + 不做未声明的事"原则。

---

## 1. 一句话定位

**Ferry 是一个面向"工具链随镜像分发、构建节点生命周期多变"场景的构建-部署引导器。它不做构建，不碰工具链，只做三件事：把探针送到对的节点、按阶段触发用户脚本、按可达性路由数据流。**

竞品不是 GitLab CI，是 `justfile + rsync + ssh + 一个状态脚本` 这个组合。Ferry 要赢的是 TTHW（time to hello world）——新节点从拿到 SSH 信息到跑通全流程的时间，不是功能数量。

---

## 2. 问题域（为什么需要 Ferry）

典型用户的真实约束：

- 交叉编译工具链以 **VM 镜像 / OCI 镜像**形式随产品迭代重发布，节点会周期性被替换。
- 构建节点**无外网**（或外网不可靠），不能 `apt install`、不能 `pip install`、不能 `git pull` 公网。
- 开发机在 NAT 后，**构建节点无法主动回连开发机**。
- 目标机可能是 arm64 板卡、busybox 环境，**只有 ssh + sh**，无 rsync、无 python。
- 开发机本地有完整开发体验（编辑器、LSP、部分平台本地构建），不需要远程 IDE。
- 构建环境与目标机环境**成对绑定**：构建机产出的二进制只能部署到特定架构的目标机。

当前手动流程：`开发 → scp/rsync 代码到构建机 → ssh 手动 cmake → 产物 scp 回开发机 → scp 到目标机 → ssh 执行部署脚本`。痛点不是单条命令难，是**每次换环境都要重新记一遍参数、探一遍可达性、拼一遍命令**。

Ferry 把这个流程压缩为：`配置一次 → ferry run → 结束`。

---

## 3. 非目标（做了就是向 CI 滑坡，禁止）

以下功能**明确不做**，即使"顺便支持一下"很容易：

- ❌ 多目标并发调度、构建队列、任务优先级
- ❌ 构建缓存（ccache、增量产物复用）
- ❌ 工具管理容器/VM 的生命周期（不 `docker run`、不启停 VM）
- ❌ 权限模型、多用户、审计日志
- ❌ Web 面板、HTTP API、常驻中心服务
- ❌ YAML 内的条件、循环、模板插值、变量引用（逻辑必须在脚本里）
- ❌ 回滚机制（失败即停 + 明确错误归属即可）
- ❌ 队列化异步执行（dev 断开连接则任务中断，不做后台任务队列。构建任务可以后台执行，但同一时刻只能有一个实例；新请求下发时**拒绝并提示**，不中断正在运行的构建。取消必须显式发起且走协作式取消，见 4.4）
- ❌ 探针在 receiver 侧持久化状态（执行完即退出，不留残留）
- ❌ 目标机安装任何 agent（目标机永远只有 ssh + sh）

**判定规则**：当你发现自己在实现"构建缓存""并发队列""权限模型""容器编排"时，立刻停止，回到本文档。

---

## 4. 核心概念

### 4.1 节点（Node）

三种角色，通过 SSH 可达：

| 角色             | 数量 | 要求                                                   |
| ---------------- | ---- | ------------------------------------------------------ |
| dev（开发机）    | 1    | 运行 Ferry CLI，持有项目源码，可以ssh访问build和target |
| build（构建机）  | 1    | Linux，无外网，需能运行静态二进制（VM 或 OCI）         |
| target（目标机） | 1    | Linux，可能只有 busybox，只需 ssh + sh                 |

**强约束**：同一项目同一时刻**只有一条激活链**：一个 dev → 一个 build → 一个 target。target 架构与 build 环境绑定。不支持多目标并发，不支持多 build 机负载均衡。

### 4.2 Target 三元组（配置的基本单位）

配置文件以 target 为键，一个 target 描述一条完整链：

```yaml
targets:
  t507_arm64: # target代称
    build:
      node: buildvm              # 填写build机名称 指向 ~/.ssh/config 的某个 Hostalias
      workdir: /work/myproj      # 构建build机上固定可写目录 脚本的.指向这个目录
      ssh_key: /home/user/.ferry/keys/id_ed25519  # 可选，up --ask-pass 自动生成
    deploy:
      node: target-t507		 # 填写target机名称 指向 ~/.ssh/config 的某个 Hostalias
      path: /opt/app             # 构建target机上固定可写目录 脚本的.指向这个目录
    toolchain:
      probe: "cat /opt/toolchain/VERSION"
      expect: "v2.4.1"
    build_cmd: "cmake -B build -DCMAKE_TOOLCHAIN_FILE=toolchains/t507.cmake && cmake --build build -j"
    out: build/out
    stages:
      pre_sync:    []
      post_sync:   [scripts/check-env.sh]
      pre_build:   []
      post_build:  [scripts/strip-symbols.sh]
      pre_deploy:  [scripts/stop-service.sh]
      post_deploy: [scripts/install-service.sh, scripts/restart.sh]
```

**配置进 git 管理**。交互式生成（`ferry up`）只是首次生成的手段，生成后写回文件、打印 diff 让用户 review。配置文件才是事实来源。

### 4.3 探针（Probe）

推送到构建机的**静态二进制**，职责严格限定：

- **`up` 阶段**：探测环境（arch、os、init、glibc、工具链版本、可用传输工具），回传探测结果，由 dev 决定后续动作。
- **`run` 阶段**：退化为**哑执行器**——接收 dev 传来的扁平执行计划（命令序列 + 环境变量 + 超时），顺序执行，流式回传每条的 exit code / stdout 尾部 / 时长。**不做任何决策**。

**探针生命周期 = 一次 run 的生命周期**，执行完即退出，不在 receiver 侧留任何持久状态、锁、日志文件。唯一例外是构建目录中的**崩溃现场标记**（见 4.4）：正常结束必然清除，仅在进程异常终止时残留，用于保护增量构建状态。

**探针的接口契约（Go，五个方法）**：

```go
type Probe interface {
    Probe() NodeInfo                              // 探测环境
    Exec(plan []Step) <-chan StepResult           // 流式执行计划
    Put(local, remote string) error               // 上传
    Get(remote, local string) error               // 下载
    Version() string                              // 版本握手
}
```

新增节点类型（如 OCI）通过实现同一接口完成，**接口不许增加方法**。契约越小，抽象越不容易漏。

### 4.4 阶段（Stage）

固定顺序，每个阶段可挂 0~N 个用户脚本，脚本声明执行位置（dev / build / target）：

```
pre_sync → sync → post_sync → pre_build → build → post_build → pre_deploy → deploy → post_deploy
```

**执行语义**：

- 脚本在独立子 shell 中执行，cwd 固定在项目根（target 侧为部署目录），环境变量显式注入，不继承 dev 的 shell 环境。
- 任一脚本失败 → **fail-fast**，打印"哪个脚本、在哪台机器、exit code、返回结果（如果）、最后 20 行日志"，不继续。
- **并发与取消**：同一 target 同时只允许一个 run。新请求遇到正在执行的构建时直接拒绝并提示（可查看 `status` 或显式取消），**不中断已有构建**。显式取消走协作式路径：向构建进程组发送 SIGTERM，给 10~30s 让构建系统自行收尾，超时才 SIGKILL，并将本次 run 标记为 `interrupted`。
- **构建状态判定**：构建开始前在构建机工作区外（rsync 不同步的位置）写入 start 标记（含 run id），正常结束（无论成功还是普通失败）后清除。下次 run 若发现标记残留且构建锁未被持有，说明上次异常中断，仅清理构建目录后走一次全量，随后恢复正常增量。构建锁用 `flock` 由构建进程持有，进程退出时内核自动释放，**不使用 PID 文件，不存在僵尸锁**。
- **失败 ≠ 中断**：脚本或构建返回非零退出码属于正常失败，增量状态可信，不触发清理；只有信号或连接断开导致的异常终止才把构建目录视为脏。
- 脚本随代码一起同步，不预装。
- 目标机侧脚本由 Ferry 传输后 `sh` 执行，**目标机永不驻留 agent**。

### 4.5 拓扑探测与数据流路由

每次 `run` 前探测三条边的可达性（`ssh -o ConnectTimeout=3 -o BatchMode=yes`）：

```
边：dev→build、build→target、dev→target
```

路由决策：

| 可达性                           | 代码流                                       | 产物流                             |
| -------------------------------- | -------------------------------------------- | ---------------------------------- |
| dev→build ✔                    | dev rsync 推工作区                           | —                                 |
| build→target ✔                 | —                                           | build 直推 target                  |
| build→target ✘，dev→target ✔ | —                                           | build → dev 拉回 → dev 推 target |
| 都不通                           | 尝试 `ProxyJump build`；仍不通则报错并提示 |                                    |

探测结果缓存本地（TTL 5 分钟），`status` 命令可查看。**拓扑路由对用户透明**，换部署机或换网段时用户零改动。

### 4.6 环境探测注入（而非工具解释）

目标机探测结果作为**环境变量注入用户脚本**，Ferry 不做解释：

```
XB_TARGET_ARCH=arm64
XB_TARGET_OS=ubuntu
XB_TARGET_OS_VER=18.04
XB_TARGET_INIT=systemd          # 或 sysvinit / none
XB_TARGET_HAS_RSYNC=1
XB_TARGET_GLIBC=2.27
XB_BUILD_TOOLCHAIN_VER=v2.4.1
```

用户脚本自行 `case "$XB_TARGET_INIT" in systemd) ... ;; sysvinit) ... ;; esac`。**Ferry 不为每种 init 系统写适配代码，适配由用户按需写在脚本里**。探测项必须收敛（十来个），不做"扫 200 个 fact"的 Ansible 化。

### 4.7 脚本结果（Script Result）

脚本向编排层回传一行诊断摘要的通道。设计为"只写、只读、不控制流"。

**契约**：工具在脚本执行前注入 `FERRY_RESULT_FILE`（临时文件路径）。脚本以单行文本写入，内容格式由开发者自定。探针在 StepResult 中原样回传 `result: string`，**工具不做解析、不做校验**。

**三态语义**（唯一必须遵守的规则）：

- 文件不存在 → `unset`，表示脚本未执行到写入点，是"进度信号"。
- 文件存在且为空 → `(empty)`，表示脚本逻辑有问题（写了但没内容）。
- 文件存在且非空 → 原样展示。

**作用域**：每脚本一个，多脚本阶段各自独立，无覆盖冲突。

**长度**：单行，上限 512 字节，超出截断并标记 `(truncated)`。多行视为格式错误，只取第一行。

**禁止**：

1. 结果不参与控制流（禁止 `when: <result>` 之类语法）。
2. 结果不跨阶段。跨阶段传递走文件系统。
3. 工具不承诺解析结果内容。开发者可自行用 `key=value;...` 等格式，但这是开发者与读者的约定，不是 Ferry 的契约。

**非目标**：不替代日志、不替代 exit code、不做断言库、不做结构化测试报告。

---

### 4.8 认证与密钥引导

默认使用 SSH 密钥认证。`--ask-pass` 或环境变量启用密码认证：

- `FERRY_BUILD_PASSWORD` / `FERRY_TARGET_PASSWORD`；两端同密码时用 `FERRY_SSH_PASSWORD`。
- 密码只存在于进程内存，通过 OpenSSH 的 `SSH_ASKPASS` 机制（`ferry-askpass` helper）传递，**不写入配置、状态或日志**，不依赖 `sshpass`。
- `ferry up --ask-pass` 做一次性引导：生成 `~/.ferry/keys/id_ed25519` 并安装到构建机和目标机；在构建机生成 `~/.ferry/keys/id_ed25519` 并把公钥安装到目标机，使 `build → target` 可用密钥直连。引导后用密钥执行探测、构建和部署。
- 若只提供密码、未做引导：目标侧脚本与产物走 `build → dev → target` 中转，**目标机密码不下发到构建机**。代价是大产物经过开发机带宽。
- 密码模式下的路由由密码是否存在决定：提供目标机密码时优先 via-dev；否则按 4.5 探测直连。

## 5. 命令面（MVP）

| 命令                                             | 语义                                                                                                                                                                                                                                                             |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ferry init`                                   | 初始化：初始化并记录ferry项目并触发模板生成流程。（本地若已存在配置文件 则提示用户忽略初始化 需要-f强制清除 或使用plan来检查配置文件可行性）                                                                                                                     |
| `ferry up --ssh user@vm [--target user@board] [--ask-pass]` | 引导：（若没有初始化项目则）交互式生成 target 配置 → 逐条验证节点契约（可达性、可写目录、工具链版本）→ 推探针（hash 握手）→ 写配置 + 打印 diff<br />（若已初始化项目则）`up` 可以重复跑，每次加一个 target。两个命令的产物都落在项目仓库里，review 后提交。`--ask-pass` 额外安装 Ferry 管理的密钥。 |
| `ferry run <target> [--ask-pass]`              | 拓扑探测 → 环境指纹比对 → 同步代码 → 阶段 hook（注入探测环境变量）→ 构建 → 按可达性路由产物 → 部署 → 状态落 dev。<br />已有实例在跑时拒绝新请求；`--cancel` 对运行中的构建发起协作式取消（SIGTERM → 宽限期 → 必要时 SIGKILL），见 4.4。`--ask-pass`/环境变量启用密码认证，见 4.8。 |
| `ferry plan <target>`                          | dry-run：打印完整命令序列 + 拓扑决策 + 路由路径，不执行。新用户第一次必用。                                                                                                                                                                                      |
| `ferry status [<target>]`                      | 读本地状态文件，表格输出各阶段状态、时长、日志路径                                                                                                                                                                                                               |
| `ferry topology`                               | 打印三边探测结果与路由决策                                                                                                                                                                                                                                       |

**第二版再评估**：`ferry watch`（dev 侧文件监听触发 run）、`ferry daemon`（build 机常驻，用轮询换异步，需明确认账）。

**不提供** `push` 单独命令——同步是 `run` 的一部分，拆开会让状态模型分裂。

---

## 6. 状态模型

**状态永远收敛到 dev 侧**。build / target 侧不留持久状态。

```jsonc
// ~/.ferry/state/<project>/<target>.json
{
  "target": "t507_arm64",
  "commit": "a1b2c3d",
  "dirty": true,                    // 工作区有未提交改动
  "toolchain": "v2.4.1",
  "topology": "build->target",      // 或 build->dev->target
  "stages": {
    "sync":   {"state": "ok",      "dur": "1.2s"},
    "build":  {"state": "ok",      "dur": "42s"},
    "deploy": {"state": "running", "since": "2026-10-18T15:30:00Z"}
  },
  "log": "~/.ferry/logs/t507_arm64-20261018-1530.log"
}
```

阶段 `state` 取值：`ok` / `failed` / `running` / `interrupted`。`interrupted` 表示进程异常终止；下次 `run` 检测到构建机上的残留标记后会先清理构建目录，再走一次全量（见 4.4）。

**代码同步语义**：MVP 只做**工作区模式**（rsync 推工作树，状态记录 commit + dirty 标记）。`--ref <commit>` 提交模式放第二版。理由：日常开发"改了就想编"是刚需，提交模式可用 `git stash && ferry run --ref` 手工模拟。

---

## 7. 关键设计决策（每条都踩过坑）

1. **推而非拉**：build 机无外网且不知道 dev 地址，拉不可行。dev 主动推。这层没有争议。
2. **"延迟回调"不存在，只有长连接流式**：探针无法回连 dev。`run` 是同步阻塞的，dev 保持 ssh 连接，探针流式回报，断开则任务中断（用户可用 tmux/nohup 保活）。工具**不承诺**"关掉终端明天看结果"。`status` 读本地收敛后的状态。
3. **探针只在 `up` 阶段决策，`run` 阶段是哑执行器**：否则会养出第二个 CLI（两套语义、两套测试、协议互相牵制）。探测结果回传 dev 后由 dev 决定后续计划。
4. **OCI 不是另一种 Linux 节点，是另一种生命周期模型**：OCI 预装探针（镜像可定制 `RUN` 层），挂载 workspace，生命周期由外部管。Ferry 只检查"在线 + 可达 + 有探针 + 有工作目录"四条契约，**不轮询等待节点上线，不 `docker run` 拉起容器**。
5. **YAML 只声明，不写逻辑**：用 pydantic 严格 schema 校验（`extra='forbid'` 拒绝未知字段），加载即报错并指出行号。出现 `when` / `if` / `for_each` / 模板插值 = 设计错误，应改写为脚本。
6. **探测结果注入环境变量，Ferry 不解释**：适配逻辑在用户脚本里。探测项收敛到十来个，不做 Ansible facts。
7. **目标机永不驻留 agent**：只传脚本 + `sh` 执行。目标机可能只有 busybox。
8. **探针执行完即退出**：不留持久状态、锁、日志。重发布 VM 时零残留，调试窗口极短。
9. **状态单点收敛到 dev**：build/OCI 侧状态随容器消失，"状态可见"会失效，所以状态必须回传并落 dev 侧文件。
10. **工具链分发是 `up` 阶段最大成本**：可能几百 MB 到数 GB。配置以归档包声明（`toolchain.bundle: /local/path.tar.zst`），版本指纹比对匹配则跳过传输，传输带校验和 + 续传。MVP 可简化，但**配置结构要留位置**。
11. **环境漂移检测**：`run` 前做轻量指纹比对（工具链版本、OS、arch、关键路径），与上次成功 run 不一致则提示"环境已变更，建议 `ferry up`"。把"编译到一半报奇怪错误"变成"提前告诉你环境变了"。
12. **host key 变化必须显式处理**：VM 重发布后 `known_hosts` 冲突会卡死首次连接。`up` 用 `StrictHostKeyChecking=accept-new`，变化时提示用户确认并 `ssh-keygen -R`，提供 `ferry up --reset` 全量重推。这是"一键"是否真的"一键"的分水岭。
13. **脚本结果是脚本 → 工具的单行只读通道**：正名为"结果"而非"变量"或"标记"，防止滑向"用输出控制阶段行为"。核心价值是 `unset` 三态暴露脚本执行进度；工具零解析，格式自由是开发者与读者的约定。校验只在探针侧做"单行 + 截断"，不做语义校验。
14. **不对构建进程强杀，增量状态只在中断后判定**：构建系统可能有内置生命周期，强杀会留下"存在但为空"的中间文件，让后续增量构建静默出错。并发请求一律拒绝，取消走 SIGTERM + 宽限期的协作式路径；只有异常终止后的残留标记才触发构建目录清理，正常失败不清理。这样既避免强杀污染，也不必每次全量构建。
15. **密码只用于认证，不落盘；目标机密码不下发到构建机**：不引入 `sshpass`（Windows 缺失、构建机未必有、密码易出现在进程列表）。通过 `SSH_ASKPASS` 传密码；`up --ask-pass` 一次性安装 Ferry 管理的密钥，之后回到纯密钥链路。仅密码模式下目标侧操作走 `build → dev → target`，避免构建机持有目标机密码。

---

## 8. 技术选型

| 角色       | 语言                        | 理由                                                                                                            |
| ---------- | --------------------------- | --------------------------------------------------------------------------------------------------------------- |
| dev CLI    | **Python 3.10+**      | 只在自己机器跑，依赖自控；YAML + pydantic 生态最顺                                                              |
| build 探针 | **Go（静态二进制）**  | `CGO_ENABLED=0` 交叉编译，任何 Linux 内核可跑，Ubuntu 18 / busybox 通吃；探针代码几百行，几乎不随项目迭代变化 |
| target 侧  | **用户写的 POSIX sh** | 目标机可能只有 busybox，绝不要求运行时                                                                          |

**双语言代价很低**，换来构建机零依赖。探针**不用 Python**：如果基镜像是Ubuntu 18，系统 Python 是 3.6（EOL），现代语法跑不了；PyInstaller 则不如Go 的静态二进制，体积更大启动更慢。

**连接优化**：`~/.ssh/config` 配 `ControlMaster auto` + `ControlPersist 10m`，rsync / ssh / scp 反复调用省握手延迟。

**传输降级**：目标机探测 `HAS_RSYNC` / `HAS_SCP` / `HAS_TAR`，降级链 `rsync → tar | ssh → base64 | ssh`。绝不假设 rsync 存在。

---

## 9. 项目结构（建议）

### 树 1：用户项目仓库（跟 git，随项目走

```
myproj/                       # 用户的业务项目
├── .ferry.yaml               # ← 配置源，跟代码一起 git 管理
├── scripts/                  # ← 用户阶段脚本，跟代码一起 git 管理
│   ├── check-env.sh
│   ├── strip-symbols.sh
│   └── install-service.sh
├── toolchains/               # ← 只放 toolchain 的"声明"，不放包体
│   └── t507.cmake
├── src/
├── CMakeLists.txt
└── .gitignore                # 排除 build/ dist/
```

**核心原则** ：`.ferry.yaml` 和 `scripts/` 是 **项目的一部分** ，`git clone` 即得完整配置。同事 clone 下来，配好自己的 `~/.ssh/config`，就能 `ferry run`。这是"配置持久化在开发机/git"原则的落地。

### 树 2：dev 本地运行时（不进 git，不属于任何项目）

```
~/.ferry/                     # 每台开发机独立
├── bin/
│   └── probe-linux-amd64     # 探针二进制，按 Ferry 版本缓存
├── state/
│   └── myproj/               # 以 project.name 为 key
│       └── t507_arm64.json
├── logs/
│   └── myproj-t507_arm64-20261018-1530.log
├── cache/
│   └── topology.json         # 三边探测结果，TTL 5 分钟
└── toolchains/               # 大文件归档包，不进 git
    └── t507-v2.4.1.tar.zst
```

**为什么状态不在项目里** ：状态含 `dirty: true`、时间戳、日志路径，属于本机事实，跨机器不同步才有意义。放项目里会污染 git、引发冲突。

### 树 3：Ferry 工具仓库（独立发布物）

```
ferry/
├── cli/                     # Python dev CLI
│   ├── ferry/
│   │   ├── commands/        # up, run, plan, status, topology
│   │   ├── topology.py      # 三边探测 + 路由决策
│   │   ├── pipeline.py      # 阶段调度 + hook 执行 + dry-run
│   │   ├── state.py         # 本地状态收敛（jsonl 追加 + json 快照）
│   │   ├── config.py        # pydantic schema + 交互式生成
│   │   └── bootstrap.py     # 探针推送 + hash 握手 + host key 处理
│   └── pyproject.toml
├── probe/                   # Go 探针
│   ├── main.go              # 接收计划、执行、流式回报
│   ├── probe.go             # Probe 接口五方法实现
│   └── go.mod
├── examples/
│   └── t507-arm64/          # 完整 target 配置 + scripts 示例
└── docs/
    ├── DESIGN.md            # 本文档
    └── ADR/                 # 决策记录
```

`examples/t507-arm64/` 只是 README 里给你抄的模板，不是运行时的配置查找路径。Ferry **永远不去** `examples/` 找配置。

## 配置发现规则

`ferry` 任何子命令启动时：

1. 从 `cwd` 向上查找 `.ferry.yaml`，找到即项目根。
2. 找不到 → 报错，提示 `ferry init`（见下）。
3. 找到多个（嵌套项目）→ 用最近的，并警告外层被忽略。

 **不使用 `.git` 作为判据** ——有些项目用 git worktree、submodule，`.git` 可能是文件不是目录；`.ferry.yaml` 是显式契约，更可靠。

---

## 10. 用户视角的成功指标

- **新仓库从 `git clone` 到第一次成功 deploy，< 10 分钟，不需读文档**（靠零配置约定 + 交互式 `up`）。
- **换构建机**：改 `~/.ssh/config` 一个 alias + `ferry up`，其余零改动。
- **换部署机**：`ferry up --target new-board`，拓扑自动重探测，其余零改动。
- **换 OCI 镜像**：如果预装探针且工作目录路径不变，`ferry run` 直接跑通；否则 `ferry up` 重新引导。
- **失败时**：错误信息明确到"哪个脚本、哪台机器、exit code、最后 20 行日志"，用户不需要翻 Ferry 源码。

---

## 11. 零配置约定（TTHW 的关键）

配置文件**只在偏离约定时才需要写**。默认约定：

- 存在 `CMakeLists.txt` → 默认 `build_cmd = "cmake -B build && cmake --build build -j"`
- `scripts/pre_*.sh` / `scripts/post_*.sh` 按命名自动挂载到对应阶段
- `dist/` 为默认产物目录
- 项目根 `.ferry.yaml` 为默认配置路径

**好 schema 让用户在需要时表达，坏 schema 强迫用户每次都表达。** 这是"反向塑造需求"和"配置税"的分界线。

---

## 12. 信任模型与安全声明

- `ferry up` 在远端执行探针二进制，等于把 dev 的信任扩散到 build 机。**只对可信 SSH 目标使用**，README 明说，不做通用分发工具。
- 用户脚本可执行任意操作（包括自动化测试、副作用操作），**Ferry 不做沙箱、不做回滚**。这是正常设计，但文档要强调：脚本的幂等性是用户责任，`post_deploy` 的重复执行不能炸。
- 探针版本与 dev CLI 版本通过 build hash 握手，不一致则 dev 自动重推探针。**不做协议向后兼容**，成本不划算。

---

## 13. 实现优先级

**MVP（第一版）**：`up` + `run` + `plan` + `status`，拓扑探测先写死 `build→target`，验证"一键引导"体验是否成立。这是整个方案的核心假设，值得先证伪。

**第二版**：`topology` 自动路由、`ferry watch`、`--ref` 提交模式、工具链归档包分发。

**第三版**：`ferry daemon`（build 机常驻，用轮询换异步，需明确认账）、OCI handler 完整实现。

**永远不做**：见第 3 节。
