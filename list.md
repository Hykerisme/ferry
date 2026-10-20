# Ferry 

版本：`ferry --version`（当前 0.1.0）

## 一句话流程

```text
ferry init      → 生成项目配置
ferry up        → 连接节点、安装探针、写入 target
ferry plan      → 预览这次会做什么
ferry run       → 同步 → 构建 → 部署
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

| 参数                     | 作用                         |
| ------------------------ | ---------------------------- |
| `--version`            | 显示版本                     |
| `--install-completion` | 安装 shell 补全              |
| `--show-completion`    | 打印补全脚本，不安装         |
| `--help`               | 查看帮助（每个子命令也支持） |

---

## 命令总览

| 命令                        | 什么时候用              | 是否改远端           |
| --------------------------- | ----------------------- | -------------------- |
| `ferry init`              | 新项目第一次接入        | 否                   |
| `ferry up`                | 新增/更换构建机或目标机 | 是（探针、可选密钥） |
| `ferry plan <target>`     | 跑之前确认步骤和路由    | 只读探测             |
| `ferry run <target>`      | 日常同步、构建、部署    | 是                   |
| `ferry status [target]`   | 查看最近一次运行状态    | 否                   |
| `ferry topology [target]` | 检查三边 SSH 是否通     | 只读探测             |

---

## `ferry init`

在当前目录生成 `.ferry.yaml`。配置应随项目进 git。

```bash
ferry init
ferry init --force        # 覆盖已有配置（覆盖前会打印 diff）
```

- 已存在配置且不加 `--force` 时退出并提示。
- 在子目录执行时，会提示外层已有的 Ferry 项目。

---

## `ferry up`

引导一个 target：校验节点 → 安装探针 → 写配置。

```bash
ferry up
ferry up --ssh buildvm --target board --name t507 --yes
```

| 参数                          | 说明                                                      |
| ----------------------------- | --------------------------------------------------------- |
| `--ssh <目标>`              | 构建机 SSH 目标或 `~/.ssh/config` 别名                  |
| `--target <目标>`           | 目标机 SSH 目标或别名                                     |
| `--name <别名>`             | 写进 `.ferry.yaml` 的 target 名，默认 `default`       |
| `--build-workdir <路径>`    | 构建机上的可写绝对路径                                    |
| `--deploy-path <路径>`      | 目标机上的部署绝对路径                                    |
| `--toolchain-probe <命令>`  | 打印工具链版本的命令，例如 `cat /opt/toolchain/VERSION` |
| `--toolchain-expect <版本>` | 期望版本；不匹配则失败                                    |
| `--build-cmd <命令>`        | 构建命令；默认在 CMake 项目用 cmake，否则 `make -j`     |
| `--out <目录>`              | 构建机上相对 workdir 的产物目录，默认 `dist`            |
| `--yes`, `-y`             | 不询问，直接写入配置                                      |
| `--reset`                   | 重新上传探针                                              |
| `--ask-pass`                | 交互输入构建机/目标机密码，并安装 Ferry 管理的密钥        |

**交互式**：省略参数时会逐项询问；已有 target 会复用原来的脚本、ignore、key 等设置。

**推荐首次流程**：

```bash
ferry init
ferry up --ssh buildvm --target board --name t507 --ask-pass
ferry plan t507
ferry run t507
```

---

## `ferry plan <target>`

Dry-run：打印节点、路由、注入的环境变量和完整步骤，不执行同步/构建/部署。

```bash
ferry plan t507
ferry plan t507 --refresh-topology
ferry plan t507 --ask-pass
```

| 参数                   | 说明                  |
| ---------------------- | --------------------- |
| `--refresh-topology` | 忽略 5 分钟的拓扑缓存 |
| `--ask-pass`         | 用密码完成只读探测    |

---

## `ferry run <target>`

执行完整链路。失败即停，状态和日志落在开发机。

```bash
ferry run t507
ferry run t507 --ask-pass
ferry run t507 --cancel
```

| 参数                   | 说明                                            |
| ---------------------- | ----------------------------------------------- |
| `--cancel`           | 对正在运行的构建发 SIGTERM，15 秒宽限后 SIGKILL |
| `--refresh-topology` | 忽略拓扑缓存                                    |
| `--reset-probe`      | 重新上传探针                                    |
| `--ask-pass`         | 交互输入密码（同一次运行内复用）                |

**并发规则**：同一 target 同时只能有一个 run。第二个 run 在同步前就会被拒绝，不会覆盖正在使用的工作区。

**中断规则**：Ctrl-C 或 `--cancel` 会保留构建 marker；下次 run 自动清理一次构建目录并全量重建，之后恢复增量。

---

## `ferry status [target]`

查看最近一次运行的状态、阶段、topology 和日志路径。

```bash
ferry status
ferry status t507
```

状态文件：`~/.ferry/state/<project>/<target>.json`
日志目录：`~/.ferry/logs/<project>/`

---

## `ferry topology [target]`

探测三条 SSH 边并打印路由决策。

```bash
ferry topology
ferry topology t507 --refresh
```

| 边                  | 用途                             |
| ------------------- | -------------------------------- |
| `dev -> build`    | 同步代码、执行构建               |
| `build -> target` | 产物直推（推荐）                 |
| `dev -> target`   | `build -> target` 不通时的中转 |

路由：

- `build -> target`：产物从构建机直接推目标机。
- `build -> dev -> target`：构建机到不了目标机，或使用了目标机密码；产物和目标脚本经开发机中转。

---

## 密码与密钥

### 一次性安装密钥（推荐）

```bash
ferry up --ask-pass
```

- 开发机生成 `~/.ferry/keys/id_ed25519`，安装到构建机和目标机；
- 构建机生成 `~/.ferry/keys/id_ed25519`，安装到目标机；
- 验证通过后，后续运行不需要密码；
- 目标机重刷后，重跑一次。

### 每次运行输入密码

```bash
ferry run t507 --ask-pass
```

或使用环境变量（适合 CI，不落盘）：

```bash
FERRY_BUILD_PASSWORD=... FERRY_TARGET_PASSWORD=... ferry run t507
FERRY_SSH_PASSWORD=... ferry run t507     # 两端同密码
```

只给目标机密码时，目标侧操作走 `build -> dev -> target`，目标密码不会下发到构建机。

---

## 环境变量

| 变量                       | 作用                                  |
| -------------------------- | ------------------------------------- |
| `FERRY_HOME`             | 覆盖本机数据目录，默认 `~/.ferry`   |
| `FERRY_PROBE_SRC`        | 覆盖探针源码目录（开发用）            |
| `FERRY_BUILD_PASSWORD`   | 构建机密码                            |
| `FERRY_TARGET_PASSWORD`  | 目标机密码                            |
| `FERRY_SSH_PASSWORD`     | 两端共用密码                          |
| `XB_PROJECT`             | 配置里的项目名                        |
| `XB_TARGET`              | 当前 target 别名                      |
| `XB_TARGET_ARCH`         | 目标架构，如 `arm64`                |
| `XB_TARGET_OS`           | 目标发行版，如 `ubuntu`             |
| `XB_TARGET_OS_VER`       | 目标系统版本                          |
| `XB_TARGET_INIT`         | `systemd` / `sysvinit` / `none` |
| `XB_TARGET_GLIBC`        | glibc 版本（可能为空）                |
| `XB_TARGET_HAS_RSYNC`    | `1` / `0`                         |
| `XB_TARGET_HAS_TAR`      | `1` / `0`                         |
| `XB_TARGET_HAS_SCP`      | `1` / `0`                         |
| `XB_BUILD_TOOLCHAIN_VER` | 工具链版本                            |
| `FERRY_RESULT_FILE`      | 脚本写一行诊断摘要的文件              |

`XB_*` 与 `FERRY_RESULT_FILE` 由 Ferry 注入，不需要自己设置。

---

## 阶段与脚本

固定顺序：

```text
pre_sync → sync → post_sync → pre_build → build → post_build
         → pre_deploy → deploy → post_deploy
```

脚本条目可写：

```yaml
stages:
  pre_sync:    ["scripts/local-check.sh@dev"]
  post_sync:   ["scripts/check-env.sh"]
  pre_build:   ["scripts/prepare.sh"]
  post_build:  ["scripts/strip.sh"]
  pre_deploy:  ["scripts/stop-service.sh"]
  post_deploy: ["scripts/install-service.sh", "scripts/restart.sh"]
```

默认位置：

| 阶段                                           | 默认执行位置 |
| ---------------------------------------------- | ------------ |
| `pre_sync`                                   | dev          |
| `post_sync` / `pre_build` / `post_build` | build        |
| `pre_deploy` / `post_deploy`               | target       |

显式覆盖：`脚本路径@dev`、`@build`、`@target`。

规则：

- 脚本在项目根（target 侧为部署目录）执行；
- 不继承开发机的 shell 环境，只拿到 `XB_*` 等显式变量；
- 任一脚本失败即停止；
- target 脚本由 Ferry 传输后 `sh` 执行，不驻留 agent。

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

| 现象                                       | 处理                                                                            |
| ------------------------------------------ | ------------------------------------------------------------------------------- |
| `no .ferry.yaml found`                   | 先 `ferry init`                                                               |
| `unknown target`                         | `ferry status` 看已有 target，或检查配置                                      |
| `another ferry run holds the build lock` | 等它结束，或 `ferry run <target> --cancel`                                    |
| `toolchain mismatch`                     | 检查 `toolchain.probe` / `expect`，必要时 `ferry up`                      |
| `host key ... changed`                   | 节点被重建；按提示 `ssh-keygen -R <host>` 后重跑 `up`                       |
| 修改了 Go 探针但远端还是旧行为             | `ferry run <target> --reset-probe`，或 `ferry up --reset`                   |
| 密码登录失败                               | 确认 OpenSSH ≥ 8.4、`ferry-askpass` 在 PATH；或改用 `up --ask-pass` 装密钥 |
| 构建机到不了目标机                         | `ferry topology` 确认路由；使用密钥引导可直连，否则走 dev 中转                |
| Windows 上 dev 脚本失败                    | 安装 Git for Windows，确保 `sh` 在 PATH                                       |
| 远端已删除的文件仍残留                     | 当前 tar 同步不删除远端文件；手工清理构建 workdir                               |
