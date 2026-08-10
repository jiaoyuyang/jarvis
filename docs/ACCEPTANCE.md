# Jarvis V1 验收清单

## 基础可用性

- [ ] 容器状态为 `healthy`
- [ ] Web 控制台必须登录后才能访问
- [ ] `codex --version` 正常，`codex login status` 显示 ChatGPT 登录
- [ ] `backend=codex`，无需 OpenAI API Key
- [ ] 控制台普通对话正常，页面不再以 `free` 模型承担主链请求
- [ ] 容器重启后配置和对话仍存在

## 钉钉入口

- [ ] 文本消息可以正常回复
- [ ] 连续三轮对话上下文正确
- [ ] 图片能够被接收和理解
- [ ] Word、PDF 或 PPT 文件能够被接收
- [ ] 长回复格式可读，不出现重复发送
- [ ] 同一钉钉消息重试不会产生重复任务

## 记忆

- [ ] 能识别用户称呼为“焦书记”
- [ ] 新会话能按需检索已确认的长期记忆
- [ ] 回答历史知识时能标注本地知识来源
- [ ] 临时信息不会被错误写成长期事实
- [ ] 普通候选进入 `memory/inbox`，明确“记住”进入 `memory/curated`
- [ ] 重复写入保持幂等，不产生相同记忆副本
- [ ] 更正记忆后旧版本保留为 `superseded`，当前视图只显示新版本
- [ ] `./scripts/memory-status.sh` 能校验账本并重建当前视图
- [ ] 备份后可检查到 memory、sessions 和 workspace 文件
- [ ] OAuth 凭据目录已备份且未进入 Git

## 订阅与 Codex

- [ ] 容器重启后无需重新登录 ChatGPT
- [ ] 能在工作区创建测试文件并读取结果
- [ ] 高风险命令会进入审批，不会静默越权执行
- [ ] 钉钉只显示最终回答，不显示 reasoning 和冗长工具轨迹
- [ ] 达到订阅限额时能明确报告，不自动降级到未知付费 API

## 安全边界

- [ ] 控制台仅监听 `127.0.0.1`
- [ ] `./scripts/check-proxy.sh` 三项连通性检查不返回 `failed`
- [ ] 使用 host 网络时，`ss -lntp` 中 8088 仍只绑定 `127.0.0.1`
- [ ] `.env`、secrets 和 runtime 未进入 Git
- [ ] 容器未挂载 Docker Socket 和宿主机敏感目录
- [ ] Codex 为 `sandbox=danger-full-access`，权限仅存在于受限 Docker 容器内
- [ ] Tool Guard、File Guard 已启用
- [ ] Skill Scanner 为 `block`
- [ ] Jarvis 拒绝执行 sudo、重启宿主机和修改自身运行配置
- [ ] `runtime/codex`、`runtime/secrets` 权限为仅部署用户可访问
- [ ] 未修改 Mihomo 配置、订阅地址和既有主备线路选择

## 切换条件

只有以上核心用例通过，且旧知识备份、迁移数量校验和恢复演练完成后，才允许将钉钉正式入口切到新 Jarvis。旧服务器最后退役。
