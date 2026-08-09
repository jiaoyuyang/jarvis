# 旧 Jarvis 数据迁移边界

## 当前阶段

V1 已进入旧知识迁移阶段。Codex Backend 不自动继承 ChatGPT 网页版记忆，
也不直接经过 QwenPaw 原生 ReMe MemoryMiddleware，因此采用“原始归档 +
整理知识 + `jarvis-memory` 显式检索”的方式。

## 必须保留的旧数据

- `/opt/codex-dingtalk/data`
- `/opt/codex-dingtalk/memory`
- `/opt/codex-workspace`
- `/opt/codex-dingtalk/.env`，仅作为加密私密备份，不进入 Git

实际路径以旧服务器只读检查结果为准。备份时必须记录文件数量、总大小和 SHA-256 校验值。

## 迁移顺序

1. 停止旧系统的记忆和任务写入，或在一致性时间点制作快照。
2. 制作完整原始备份，保留一份不参与转换的副本。
3. 盘点长期记忆、会话、任务、知识文件和上传文件。
4. 使用 `scripts/migrate-legacy-knowledge.sh` 写入按时间戳隔离的归档区和整理区。
5. 校验数量、关键事实、日期、来源和中文编码。
6. 由用户抽样确认检索结果；V1 直接使用本地文本检索，不依赖付费 Embedding。
7. 完成备份恢复演练后才允许退役旧服务器。

## 明确禁止

- 不把旧 `.env`、API Key、钉钉密钥提交到 GitHub。
- 不直接用转换结果覆盖唯一原件。
- 不把全部聊天记录无差别写入长期记忆。
- 不在旧服务器删除前跳过恢复演练。

## 目录映射

| 旧目录 | 新目录 | 用途 |
|---|---|---|
| `memory/user` | `knowledge/imports/<时间>/profile` | 用户资料与偏好 |
| `memory/pingan` | `knowledge/imports/<时间>/enterprise` | 企业架构知识 |
| `memory/projects` | `knowledge/imports/<时间>/projects` | 项目知识 |
| `memory/standards` | `knowledge/imports/<时间>/standards` | 输出与工作标准 |
| `memory/history` | `knowledge/imports/<时间>/decisions` | 决策与经验 |
| `data`（排除凭据、缓存和日志） | `knowledge/archive/<时间>` | 记忆、任务与变更记录归档 |
| `/opt/codex-workspace` | `knowledge/archive/<时间>/codex-workspace` | 旧工作文件归档 |

`recent_history.json` 等历史对话只进入归档，不直接作为长期事实。新产生的
长期记忆候选写入 `memory/inbox/YYYY-MM-DD.md`，确认后再整理。
