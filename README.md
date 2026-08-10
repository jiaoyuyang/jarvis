# Jarvis

Jarvis 是焦玉阳的 7x24 个人智能助理。本仓库不再自研通用 Agent 框架，而是以 [QwenPaw](https://github.com/agentscope-ai/QwenPaw) 为运行底座，维护 Jarvis 自己的人设、安全策略、部署方式和数据迁移工具。

当前 V1 验证范围：

- Ubuntu + Docker Compose 单机部署
- 钉钉 Stream 入口
- Web 控制台与登录认证
- 使用 ChatGPT 订阅 OAuth 的 Codex 主智能体
- QwenPaw 会话、文件、Skills、MCP 和本地知识
- 两级持续记忆、追加式版本账本与冲突留痕
- 钉钉材料接收、管理写作和项目行动闭环
- Jarvis 专属人设与安全边界
- 运行数据、密钥和代码完全分离
- 为后续华为小艺 A2A 入口预留能力

## 设计原则

1. **跟随上游**：不 Fork 或复制 QwenPaw；采用官方 Codex Harness，仅对锁定版本应用可审计、构建期校验的最小兼容补丁。
2. **私有数据不入库**：记忆、会话、密钥和文件仅保存在服务器 `runtime/`。
3. **容器即边界**：Jarvis 不挂载 Docker Socket，不获得宿主机目录和特权权限。
4. **订阅优先**：Codex 使用 ChatGPT OAuth，不要求 OpenAI API Key 或
   OpenAI Platform 计费。
5. **旧服务器最后退役**：完成备份、恢复和验收前不删除旧环境。

腾讯云部署使用服务器既有 Mihomo：本地代理 `127.0.0.1:7890`。镜像基础层
从 AgentScope 阿里云仓库拉取；构建和运行阶段通过 host 网络复用 Mihomo。
自定义镜像强制 QwenPaw 只监听 `127.0.0.1`，不会因为 host 网络暴露控制台。

## 目标架构

QwenPaw 负责钉钉入口、会话、权限和工具管理；默认 Jarvis 智能体直接使用
Codex 后端；Codex 通过 ChatGPT 订阅 OAuth 获取模型能力；个人知识与记忆
只保存在服务器 `runtime/`，通过 `jarvis-memory` Skill 显式检索。

免费模型仅作为手工回退选项，不在正常请求链路中。

## 快速部署

Ubuntu 服务器执行：

```bash
git clone https://github.com/jiaoyuyang/jarvis.git
cd jarvis
./scripts/install.sh
```

安装脚本会：

- 自动创建 `.env` 和随机 Web 管理密码；
- 基于固定版本 QwenPaw 镜像构建包含 Codex runtime 的 Jarvis 镜像；
- 创建独立的 data、secrets、backups 目录；
- 启动服务并等待健康检查；
- 首次写入 Jarvis 人设、安全配置和本地记忆 Skill。

查看生成的登录信息：

```bash
grep '^QWENPAW_AUTH_' .env
```

控制台只监听服务器本机。电脑上建立 SSH 隧道：

```bash
ssh -L 8088:127.0.0.1:8088 ubuntu@YOUR_SERVER_IP
```

随后打开 `http://127.0.0.1:8088`。

## 首次配置顺序

1. 登录 Web 控制台。
2. 执行 `./scripts/codex-login.sh`，使用现有 ChatGPT 账号完成设备登录。
3. 执行 `./scripts/enable-codex.sh`，把默认 Jarvis 切换到 Codex。
4. 执行 `./scripts/codex-status.sh`，确认 `backend=codex`。
5. 在控制台和钉钉分别完成验收。

不需要配置模型供应商或 OpenAI API Key。模型与推理强度默认跟随 ChatGPT
账户可用项，也可以在 QwenPaw 对话工具栏中选择。

已有 2.0.1 部署升级：

```bash
cd ~/jarvis
git pull
chmod +x scripts/*.sh
./scripts/upgrade-subscription.sh
./scripts/codex-login.sh
./scripts/enable-codex.sh
./scripts/codex-status.sh
```

升级脚本会先检查 Mihomo、Docker Hub、PyPI 和 OpenAI 登录站点的代理
连通性，并更新私有 `.env`。它不会修改 Mihomo 配置，也不会自动切换梯子猫
或一元机场线路。

详细步骤见 [Ubuntu 部署](docs/DEPLOY_UBUNTU.md) 和 [验收清单](docs/ACCEPTANCE.md)。旧系统知识和记忆迁移见 [迁移边界](docs/MIGRATION.md)，持续记忆见 [持续记忆](docs/MEMORY.md)，材料与项目闭环见 [工作材料闭环](docs/WORKFLOWS.md)。

## 常用命令

```bash
./scripts/status.sh
./scripts/backup.sh
./scripts/apply-persona.sh
./scripts/memory-status.sh
./scripts/workflow-status.sh
./scripts/codex-status.sh
./scripts/check-proxy.sh
./scripts/migrate-legacy-knowledge.sh --dry-run
docker compose logs -f --tail=200 jarvis
docker compose restart jarvis
```

切回 QwenPaw 原生模型可执行 `./scripts/disable-codex.sh`。这只切换智能体
后端，不删除 OAuth、知识、会话或备份。

## 版本说明

QwenPaw 的直接 Codex Backend 从 2.1.0 beta 开始提供，因此本版本固定在
`v2.1.0-beta.2` 并安装其对应的 `openai-codex==0.144.4`。Jarvis 派生镜像对该锁定版本应用 final-only 兼容补丁：Codex 内部仍可规划、检索和调用工具，但钉钉仅收到每轮最终答复。升级脚本会先备份现有数据；完成验收前保留备份和旧服务器。

## 上游与许可证

Jarvis 部署层采用 Apache-2.0 许可证。QwenPaw 由 AgentScope 团队维护并采用 Apache-2.0 许可证；其源码、镜像和第三方依赖分别遵循各自许可证。
