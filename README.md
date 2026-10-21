# Ferry

[ [简体中文](README.md) | [English](README_EN.md) ]

**Ferry是一个轻量级的自动化部署工具，专为个人和工作室级的定制化构建流程。** <br> 
服务于以下结构：`[开发节点] -> [构建节点] -> [目标部署节点]`

Ferry把 **「同步代码 → 构建 → 产物路由 → 部署」** 的流程压缩成一次 `ferry run` ，<br> 
设置好SSH的HostAlias之后，通过Yaml和Shell脚本来管理整个部署流程。

Ferry将整个流程抽象为以下三个阶段：

1. 把探针和代码送到构建机（支持git管理和持久化）；
2. 按阶段触发用户脚本；
3. 按 SSH 可达性路由产物和触发自动化脚本。

传统的方案之一是 `Justfile + rsync + sshpass`，我们只是将整个流程设计的更人性化，并且**对异构和交叉构建更加友好**。 <br> 
**相比于CI方案，从零开始部署的成本也更低**，因为**Ferry并不依托于存储服务器或容器化Runner节点，针对只有busybox的平台也能更好工作。** <br> 
就算你使用k230或MaixCAN之类的**极简busybox，也可以使用Ferry来定制化流程**，可能一定程度上可以绕过低效或设计不友好的原构建流程。

Ferry不强依赖于你已有的Makefile/CMake/GNinja经验，追求**通用化和多样化的组合式构建流程**。 <br> 
换言之，Ferry更像是一个构建适配器，通过Yaml配置化你的项目级构建节点和目标节点，通过Shell脚本来自定义你的构建流程。 <br> 
**任何会使用Unix命令行的人都可以轻松使用该工具**

此外，由于探针设计轻量且尽可能的无依赖，构建机可以是无状态的虚拟机节点或者OCI容器。

## 快速开始

```bash
# 初始化ferry项目
ferry init
# 或者初始化一个本地单机项目
# ferry init --local

## 进入.ferry.yaml编辑节点配置

ferry plan t507      # dry-run：预览你的设置
ferry run t507       # 同步 → 构建 → 部署
ferry status # 查看上一次的构建情况
ferry topology # 快速检查三点可达性
```

## 什么是.ferry.yaml?
**`.ferry.yaml`是ferry用来识别ferry项目配置的文件** <br>
如果你用过其他的CI工具或者配置化容器平台，应该对这样的文件并不陌生。<br>
事实上，我们在设计时大量参考了Github和Gitlab的CI/CD工具配置，并抽象出最核心最易于理解的部分，以让你**快速上手**。

通过这样一个文件来使用ferry有两个好处：
1. **配置即构建**：如果你的项目同时有多个开发者，`.ferry.yaml`可以为**每个开发者保持相似的构建体验**，并将构建流程显化。<br> 
对于有云端构建/异构构建需求的个人开发者或工作室，你**不需要**配置一个复杂的分布式开发平台或CI工具，就能**快速形式化和规范流程**
2. **配置即项目**：我们**精心设计**了文件的配置项，不仅可以**快速适配任何项目开发体验**，**降低上手时间**，<br> 
还能作为你项目的*快速构建流程说明*，让任何新来的人不需要学习平台特有的构建方式，就能一键启动构建。
3. **配置即预设**：小型化不意味着简洁。往往小团队更加频繁的面对跨平台开发问题，而**我们支持在一个项目里容纳多种预设**。<br> 
或许今天你需要部署到自己的电脑上测试，明天需要部署到树莓派上，后天需要部署到荔枝派上...**没关系，你可以拥有很多种流程。**

### 我讨厌交互式配置，请告诉我怎么写.ferry.yaml
完整中文模板请见[中文配置文件模板](template_zh.ferry.yaml)
<details>
<summary>[点击查看] 我很喜欢一页就能写完的yaml，不知道你是否也这么觉得。</summary>

```yaml
version: 1
targets:
  起一个好听的名字:  # 目标机昵称，不影响构建流程
    sync:
      ignore:
        - .git
        - node_modules
    # ---------------- 构建节点 ----------------
    build:
      node: buildnode #（在~/.ssh/config里面配置HostAlias）
      # 支持内联HostAlias配置，详见模板，注意内联模式时.ferry.yaml不会同步传输。
      workdir: "/workspace" # <<代码会被发送到这里
      out: "./build/out" # >>预期中的构建产物位置（会递归的被复制到deploy机器哦，像筛选传输内容就用数组吧）

      # 执行流程: before -> sync -> run -> after
      before_build: "echo prepare to build" # 可选
      run_build: ["./scripts/build.sh"]
      after_build: "echo build completed" # 可选

    # ---------------- 目标节点 ----------------
    deploy:
      node: myboard
      workdir: /opt/app

      # 执行流程: before -> sync -> run -> after
      before_deploy: "echo prepare to deploy" # 可选
      run_deploy: "echo deploying" # 必选
      after_deploy: "echo deploy completed" #可选
```

</details>


### 我希望完全本地构建，ferry好像不太适合我。
**但是，等等！（译制腔）**
恭喜你，ferry同样支持本地构建。<br>
事实上，ferry的节点并不局限于ssh目标，我们提供了两个代词来表达**节点复用**：
- dev.node 代表你的开发机节点
- build.node 代表你的构建机节点

换句话说，你完全可以：<br>
`dev.node -> dev.node -> dev.node` *（完全本地构建，而且不经过OpenSSH）* <br>
`dev.node -> build.node -> build.node` *（在构建机上执行deploy脚本，云端构建，云端部署（比如服务端部署））* <br>
`dev.node -> build.node -> dev.node` *（在开发机上执行deploy脚本，云端构建，本地部署（比如烧录单片机））* <br>
...（以下省略）

<details>
<summary>[点击查看] 其实和上面的没多大区别，就是所有的node都变成了node: dev.node</summary>

```yaml
version: 1
targets:
  我的电脑: # 不影响构建流程，假设我们用这个预设来进行本地构建
    sync:
      ignore:
        - .git
        - node_modules
    # ---------------- 构建节点 ----------------
    build:
      node: dev.node #（在~/.ssh/config里面配置HostAlias）
      # 支持内联HostAlias配置，详见模板，注意内联模式时.ferry.yaml不会同步传输。
      workdir: "/workspace" # <<代码会被发送到这里
      out: "./build/out" # >>预期中的构建产物位置（会递归的被复制到deploy机器哦，像筛选传输内容就用数组吧）

      # 执行流程: before -> sync -> run -> after
      before_build: "echo prepare to build" # 可选
      run_build: ["./scripts/build.sh"]
      after_build: "echo build completed" # 可选

    # ---------------- 目标节点 ----------------
    deploy:
      node: dev.node
      workdir: /tmp

      # 执行流程: before -> sync -> run -> after
      before_deploy: "echo prepare to deploy" # 可选
      run_deploy: "假装你需要烧录一个物理设备的指令" # 必选
      after_deploy: "echo deploy completed" #可选
 
```

</details>

## 命令总览

[list.md](list.md)有更详细的说明

| 命令                        | 作用                                     | 作用节点                              |
| :-------------------------- | ---------------------------------------- | ------------------------------------- |
| `ferry init`              | 初始化项目，生成配置模板（`--local` 生成单机 target） | 开发机                                |
| `ferry up`                | 为构建做准备，进行节点预连接，安装探针等 | 所有节点                              |
| `ferry plan <target>`     | 流程预览，检查配置是否有误               | 无                                    |
| `ferry run <target>`      | 进行代码同步、构建、部署。               | 所有节点                              |
| `ferry status [target]`   | 查看上次构建状态                         | 无                                    |
| `ferry topology [target]` | 进行网络拓扑检查                         | 所有节点 <br> <br />（纯网络请求） |

## 安装

```bash
uv tool install ferry-tool
# 或：pipx install ferry-tool
ferry --version
```

发布包已内置 linux/amd64、linux/arm64、linux/armv7 探针 <br> 
开发端依赖于python，应该可以多平台运行。

若需要特殊平台的Ferry，自行构建十分容易（依赖于python环境和uv管理器）：

```bash
git clone <repo> && cd ferry
uv sync
uv run ferry --help
```

## 构建Ferry的前置条件

- Python 3.10+（用 `uv tool` / `pipx` 安装时自动处理）
- **（可选）从源码运行时**需要 Go 1.22+（用于交叉编译探针；发布 wheel 不需要）
- 是的，没了。（我们想尽可能的做的轻一些）

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

## 安全性
还没想好怎么编，如果你发现严重bug请告知。


## 中断语义（如果你感兴趣的话）

- 同一 target 的新 `run` 会在 sync 之前尝试获取构建锁，拿不到就拒绝；不会杀死正在运行的构建，也不会覆盖它正在使用的工作区。 <br> 
  （不会自动杀死构建进程，避免构建流程中断）
- `ferry run <target> --cancel` 会请求构建停止：远程构建发送 `SIGTERM`，探针把信号转发给构建进程组（比如docker和cmake会响应该信号并执行清理流程），等待 15 秒后才 `SIGKILL`；本地运行在 POSIX 上发送 `SIGINT`，Windows 上使用 `taskkill /T`。
- `run_build` 前写入 marker，正常结束（成功或普通失败）删除。异常中断会保留 marker；下次 `run` 检测到后清理 `out` 的第一段目录并做一次全量。`before_build`/`sync` 阶段中断不写 marker。
- 锁使用 `flock`（Windows 使用 `msvcrt.locking`），进程退出时由内核释放，避免僵尸 PID 文件。

## 本机临时数据列表

```
~/.ferry/bin/      # 按 hash 缓存的探针二进制
~/.ferry/keys/     # Ferry 管理的密钥
~/.ferry/state/    # 每个 project/target 的最近一次运行状态
~/.ferry/logs/     # 运行日志
~/.ferry/cache/    # 拓扑探测缓存（TTL 5 分钟）
```

## 已知限制

- （仅非本地模式）目标机必须提供 `tar` + `sh`；
- 增量传输模式，删除文件并不会自动在远端机上同步删除
- 【设计如此】（仅节点复用情景）工具不阻止`out`目录重叠，只会发出警告
- 密码模式下目标侧操作经 dev 中转；需要直连时先用 `up --ask-pass` 安装密钥。
- 【设计如此】 不提供额外的工具链或功能，传输以外的操作取决于于用户脚本。


## 关于贡献这个项目

我们并不阻止任何AI提交代码，前提是：
1. 你对AI提交的代码完全负责，而且保证已经人工审查过提交内容
2. 了解你的代码带来的副作用。能够清晰的说明为什么项目需要你的补丁（比如bug修复，或者严重功能缺失）
3. 遵循本项目的开发哲学，不过多添加功能、不影响主要流程、不添加上手难度

对于以下目的的提交，可能会遭到拒绝：
1. 无任何说明的提交
2. 添加改动或功能并不符合项目需求
3. 上手困难
4. 应用面窄或过广，或使得项目接近传统CI工具
5. 引入外部依赖

