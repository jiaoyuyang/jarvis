# Jarvis

Jarvis 是焦玉阳的 7x24 个人智能助理。本仓库不再自研通用 Agent 框架，而是以 [QwenPaw](https://github.com/agentscope-ai/QwenPaw) 为运行底座，维护 Jarvis 自己的人设、安全策略、部署方式和数据迁移工具。

当前 V1 验证范围：

- Ubuntu + Docker Compose 单机部署
- 钉钉 Stream 入口
- Web 控制台与登录认证
- QwenPaw 会话、文件、任务和 ReMe 记忆
- Jarvis 专属人设与安全边界
- 运行数据、密钥和代码完全分离
- 为后续华为小艺 A2A 入口预留能力

## 设计原则

1. **跟随上游**：不复制或魔改 QwenPaw 核心源码。
2. **私有数据不入库**：记忆、会话、密钥和文件仅保存在服务器 `runtime/`。
3. **容器即边界**：Jarvis 不挂载 Docker Socket，不获得宿主机目录和特权权限。
4. **先钉钉验收**：小艺、Codex MCP 和旧记忆迁移在主链稳定后逐项启用。
5. **旧服务器最后退役**：完成备份、恢复和验收前不删除旧环境。

## 快速部署

Ubuntu 服务器执行：

```bash
git clone https://github.com/jiaoyuyang/jarvis.git
cd jarvis
./scripts/install.sh
```

安装脚本会：

- 自动创建 `.env` 和随机 Web 管理密码；
- 拉取 QwenPaw 官方镜像；
- 创建独立的 data、secrets、backups 目录；
- 启动服务并等待健康检查；
- 首次写入 Jarvis 人设和安全配置。

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
2. 在模型设置中配置模型供应商和 API Key。
3. 先在控制台完成一次普通对话。
4. 在频道设置中启用钉钉，填写 Client ID 和 Client Secret。
5. 在钉钉中完成验收用例。

详细步骤见 [Ubuntu 部署](docs/DEPLOY_UBUNTU.md) 和 [验收清单](docs/ACCEPTANCE.md)。旧系统知识和记忆迁移见 [迁移边界](docs/MIGRATION.md)。

## 常用命令

```bash
./scripts/status.sh
./scripts/backup.sh
./scripts/apply-persona.sh
docker compose logs -f --tail=200 jarvis
docker compose restart jarvis
```

## 上游与许可证

Jarvis 部署层采用 Apache-2.0 许可证。QwenPaw 由 AgentScope 团队维护并采用 Apache-2.0 许可证；其源码、镜像和第三方依赖分别遵循各自许可证。

