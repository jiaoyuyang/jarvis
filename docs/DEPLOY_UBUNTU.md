# Ubuntu 部署

## 1. 前置条件

- Ubuntu 22.04 或更高版本
- Docker Engine
- Docker Compose Plugin
- 当前用户可执行 `docker info`
- 服务器能够访问 Docker Hub 或 AgentScope 阿里云镜像仓库

不要在部署前删除旧 `codex-dingtalk`。新 Jarvis 使用独立容器、端口和目录，可以并行验证。

## 2. 安装

```bash
git clone https://github.com/jiaoyuyang/jarvis.git
cd jarvis
chmod +x scripts/*.sh
./scripts/install.sh
```

如 Docker Hub 访问较慢，在 `.env` 中改为：

```dotenv
QWENPAW_IMAGE=agentscope-registry.ap-southeast-1.cr.aliyuncs.com/agentscope/qwenpaw:latest
```

然后重新执行 `./scripts/install.sh`。

## 3. 访问控制台

服务只绑定 `127.0.0.1`，不直接暴露公网端口。从电脑建立 SSH 隧道：

```bash
ssh -L 8088:127.0.0.1:8088 ubuntu@YOUR_SERVER_IP
```

浏览器访问 `http://127.0.0.1:8088`。账号密码保存在服务器仓库目录的 `.env`，该文件已被 Git 忽略。

## 4. 模型与钉钉

在控制台中依次完成：

1. 设置 → 模型：添加模型供应商和 API Key，并选择活动模型。
2. 控制台对话：发送“你是谁”，确认回复正常。
3. 控制 → 频道 → DingTalk：启用频道并填写 Client ID、Client Secret；关闭“显示思考过程”“显示工具调用信息”和“显示工具结果信息”。
4. 钉钉开发者后台确认机器人使用 Stream 模式。
5. 在钉钉单聊机器人执行验收清单。

不要把真实 Client Secret 或模型 API Key 写入 Git 仓库、部署文档或聊天截图。

如果控制台仍显示 `Default`/`QwenPaw` 而不是 Jarvis，重新应用默认智能体配置：

```bash
./scripts/apply-persona.sh
./scripts/harden-security.sh
docker compose restart jarvis
```

## 5. 运维

```bash
./scripts/status.sh
docker compose logs -f --tail=200 jarvis
docker compose restart jarvis
./scripts/backup.sh
```

升级前先备份。V1 验证期间不要使用无版本控制的清理脚本，也不要挂载 `/var/run/docker.sock`、`/opt`、`/home/ubuntu` 或宿主机根目录到容器。
