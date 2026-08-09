# Ubuntu 部署

## 1. 前置条件

- Ubuntu 22.04 或更高版本
- Docker Engine
- Docker Compose Plugin
- 当前用户可执行 `docker info`
- 服务器能够通过现有 Mihomo 访问 Docker Hub
- 服务器既有 `mihomo.service` 正常运行，本地代理为 `127.0.0.1:7890`

不要在部署前删除旧 `codex-dingtalk`。新 Jarvis 使用独立容器、端口和目录，可以并行验证。

## 2. 新安装

```bash
git clone https://github.com/jiaoyuyang/jarvis.git
cd jarvis
chmod +x scripts/*.sh
./scripts/install.sh
```

安装完成后使用 ChatGPT 订阅登录并启用 Codex：

```bash
./scripts/codex-login.sh
./scripts/enable-codex.sh
./scripts/codex-status.sh
```

`codex-login.sh` 会显示设备登录网址和一次性验证码。在电脑浏览器中使用
现有 ChatGPT 账号确认即可。OAuth 数据保存在 `runtime/codex/`，容器重启
和重新构建不会丢失。

默认使用 AgentScope 官方 Docker Hub 镜像，并通过服务器现有 Mihomo 完成
镜像下载、构建和 Codex 运行时访问：

```dotenv
QWENPAW_IMAGE=docker.io/agentscope/qwenpaw:v2.1.0-beta.2
JARVIS_HTTP_PROXY=http://127.0.0.1:7890
JARVIS_HTTPS_PROXY=http://127.0.0.1:7890
JARVIS_ALL_PROXY=socks5h://127.0.0.1:7890
```

部署前可只读检查代理：

```bash
./scripts/check-proxy.sh
```

首次构建前执行 `./scripts/enable-server-proxy.sh`。它会把 Mihomo 地址写入
Jarvis 环境，并创建 `/etc/systemd/system/docker.service.d/jarvis-proxy.conf`，
随后重启一次 Docker，使基础镜像的鉴权与下载也经过代理。该操作不会删除
容器数据、Jarvis 知识或记忆；升级脚本会自动执行这一步。

脚本不会修改 `/etc/mihomo/config.yaml`、订阅、节点选择或 `mihomo.service`。正常线路
为“梯子猫 → TW｜台湾 01”；只有主线路连续异常时才按既有运维规则切换到
一元机场。

## 3. 访问控制台

服务只绑定 `127.0.0.1`，不直接暴露公网端口。从电脑建立 SSH 隧道：

```bash
ssh -L 8088:127.0.0.1:8088 ubuntu@YOUR_SERVER_IP
```

浏览器访问 `http://127.0.0.1:8088`。账号密码保存在服务器仓库目录的 `.env`，该文件已被 Git 忽略。

## 4. 从现有 QwenPaw 2.0.1 升级

```bash
cd ~/jarvis
git pull
chmod +x scripts/*.sh
./scripts/upgrade-subscription.sh
```

升级脚本先制作停机一致性备份，再构建新镜像。它不会在 OAuth 成功前强制
切换后端。构建过程中基础镜像走 AgentScope 官方 Docker Hub 仓库，`apt`、PyPI 和
容器内 Codex 通过 `127.0.0.1:7890` 访问现有 Mihomo。构建完成后执行：

```bash
./scripts/codex-login.sh
./scripts/enable-codex.sh
./scripts/codex-status.sh
```

如果 Codex 验证未通过，执行 `./scripts/disable-codex.sh` 可切回 QwenPaw
原生后端。需要回退 QwenPaw 版本时，应同时使用升级前备份恢复对应数据，
不要用 2.0.1 直接读取已经迁移过的 2.1 数据目录。

## 5. Codex 与钉钉

在控制台中依次完成：

1. 设置 → 智能体 → Jarvis，确认后端为 Codex、账户显示已连接。
2. 控制台对话发送“你是谁”，确认回复正常。
3. 对话工具栏按需选择当前订阅可用的模型与推理强度。
4. 控制 → 频道 → DingTalk：确认频道仍启用；关闭“显示思考过程”“显示工具调用信息”和“显示工具结果信息”。
5. 钉钉开发者后台确认机器人使用 Stream 模式。
6. 在钉钉单聊机器人执行验收清单。

本方案不需要 OpenAI API Key。不要把钉钉 Client Secret、OAuth 文件或其他
凭据写入 Git 仓库、部署文档和聊天截图。

如果控制台仍显示 `Default`/`QwenPaw` 而不是 Jarvis，重新应用默认智能体配置：

```bash
./scripts/apply-persona.sh
./scripts/harden-security.sh
docker compose restart jarvis
```

## 6. 旧知识迁移

先盘点默认路径，不复制：

```bash
./scripts/migrate-legacy-knowledge.sh --dry-run
```

确认后执行：

```bash
./scripts/migrate-legacy-knowledge.sh
./scripts/apply-persona.sh
```

脚本只读旧目录，导入结构化知识并生成 SHA-256 清单；不会复制 `.env`、
密钥、Git 元数据、虚拟环境、日志和旧备份。宿主机先在
`runtime/import-staging/` 形成只读迁移检查点，再由容器复制到 Jarvis
workspace，避免目录属主差异导致知识遗漏。它不是完整服务器备份的替代品。

## 7. 运维

```bash
./scripts/status.sh
docker compose logs -f --tail=200 jarvis
docker compose restart jarvis
./scripts/backup.sh
./scripts/codex-status.sh
```

升级前先备份。V1 验证期间不要使用无版本控制的清理脚本，也不要挂载 `/var/run/docker.sock`、`/opt`、`/home/ubuntu` 或宿主机根目录到容器。

当前 Compose 使用 host 网络是为了让容器访问只绑定在宿主机回环地址的
Mihomo。Docker 镜像中已将 QwenPaw 的监听地址从 `0.0.0.0` 固定为
`127.0.0.1`；不要删除这项约束。
