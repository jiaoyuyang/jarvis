# Jarvis

Jarvis 是从 `codex-dingtalk` 演进而来的个人智能助理项目。当前版本以钉钉机器人为主要入口，复用现有任务执行、文件收发、插件匹配、短期历史和受控记忆能力，并逐步向统一 Orchestrator 架构迁移。

## 当前状态

- 代码基线：现网源码的净化快照
- 主要入口：钉钉 Stream / WebSocket
- 执行内核：Codex CLI + 单并发执行池
- 扩展方式：插件注册与意图路由（部分仍处于 shadow mode）
- HTTP Gateway：已具备基础 FastAPI 骨架，尚未成为统一主链

## 目录

- `bot.py`：当前钉钉主链与流程编排
- `core/`：任务、执行池、路由、记忆与恢复组件
- `plugins/`：能力匹配插件
- `gateway/`：HTTP Gateway 原型与部署样例
- `render/`：卡片与 Markdown 渲染
- `tools/`：PPT 渲染、插件测试和摘要维护工具
- `docs/`：数据迁移契约和通用格式说明

## 安全边界

本仓库只保存源码和非敏感配置样例。以下内容不得提交：

- `.env`、密码、令牌、API Key、SSH Key
- 任务数据库、聊天历史、用户记忆和上传文件
- 运行日志、缓存、备份、重启请求和回滚状态
- Codex 本地运行目录及安装标识
- 生产环境运维手册、服务器拓扑、回滚凭据及个人上下文

## V1 演进原则

1. 保留已验证的传输、执行、文件和任务能力。
2. 外层补齐统一消息模型、Orchestrator、Tool Policy 和 Result Validator。
3. 先 shadow mode 验证，再逐步切换主链，并始终保留回滚路径。
4. 记忆治理遵循“任务优先、记忆后置、用户可查可删”。
5. 不引入新的 Agent 框架或多 Agent 自治。

> 当前仓库是迁移基线，不代表已完成 Jarvis V1 安全加固。生产部署前必须完成身份白名单、最小权限、下载边界和自维护权限隔离。

## 配置与迁移

生产环境必须通过 `JARVIS_DATA_DIR`、`JARVIS_MEMORY_DIR` 和 `JARVIS_WORKSPACE` 将私有运行数据放在 Git 仓库之外。部署和迁移步骤见 `docs/MIGRATION.md`。
