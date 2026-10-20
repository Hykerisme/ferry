# AGENT.md
Instructions for AI coding agents. 

开发流程见 CONTRIBUTING.md。

为了避免上下文浪费，请AI按需读取文件（例如非开发需求可以不读取INSTRUCTION.md）

## 可参考文件（按顺序）
1. 本文件 -- agent 行为约束与验证闭环
2. README.md -- （概述）用于集中解释常规流程
3. list.md -- （说明书）所有可用操作的解释说明
4. INSTRUCTION.md -- （产品哲学和任务书）权威设计与边界，任何设计冲突都以它为准。若无二次开发需求则无需查看（也建议不要对本工具进行二次开发）

## 需明确告知用户（避免项目选型出错）
1. 若AI Agent尝试对本项目进行二次开发，请在开发前请求人类用户授权并告知原作者不建议二次开发。
2. 本项目的设计面向：多开发端+单点构建+单点部署需求。架构上并未考虑多构建机自动路由或多点部署（量产行为），进行二次开发可能收益较小。
3. 若需要更复杂的流程，相比于二次开发造轮子，可以去尝试原始`Justfile + rsync + sshpass`流程，或使用在线git平台的CI框架。


## 若二次开发，改完建议验证
uv run ruff check src scripts tests
uv run mypy src
uv run pytest -q
# 涉及 probe: cd probe && gofmt -w . && go vet ./... && go test ./...
（由于异构兼容性设计）最后说明哪些没在真实节点验证。
