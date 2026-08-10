# Jarvis 持续记忆

## 目标

Jarvis 的记忆采用“两级内容 + 追加式账本”：日常会话只沉淀简洁候选，经过
明确确认或整理后才进入当前有效长期记忆。历史版本不静默覆盖，敏感凭据拒绝
写入，所有数据随 `runtime/data` 一起备份。

## 数据结构

| 路径 | 作用 | 是否手工修改 |
| --- | --- | --- |
| `memory/inbox/YYYY-MM-DD.md` | 日常候选及来源 | 否 |
| `memory/ledger.jsonl` | 追加式审计账本、事实源 | 否 |
| `memory/state.json` | 当前机器状态 | 否，可重建 |
| `memory/curated/` | 当前有效长期记忆 | 否，可重建 |
| `knowledge/` | 迁移知识与原始归档 | 仅通过迁移工具 |

旧的 `PROFILE.md`、历史 `memory/inbox` 和 `knowledge/imports` 不会被自动改写；
它们继续作为历史知识参与检索。账本启用后的新增记忆进入新结构。

## 写入规则

- 普通会话：仅在产生稳定事实、偏好、项目决策或可复用标准时写入收件箱。
- 明确“记住”：直接成为当前有效长期记忆。
- 内容冲突：必须建立“旧版本 → 新版本”的取代关系。
- 停止使用：创建逻辑停用记录；不物理删除历史账本。
- 密码、Token、API Key、SSH Key、身份证号和银行卡号拒绝写入。

## 运维

部署或更新技能：

```bash
./scripts/apply-persona.sh
docker compose restart jarvis
```

校验账本、重建当前视图并查看候选数量：

```bash
./scripts/memory-status.sh
```

创建备份：

```bash
./scripts/backup.sh
```

备份默认保留最近 3 份，可在 `.env` 设置：

```bash
JARVIS_BACKUP_RETENTION=3
```

每次新备份完成并通过 SHA-256 校验后，才会删除超出保留数量的旧手工备份。

## 恢复原则

`memory/ledger.jsonl` 是新增持续记忆的事实源。`state.json` 或 `curated/` 损坏时，
执行 `./scripts/memory-status.sh` 即可校验账本并重建；账本本身损坏时，从最近一份
校验通过的服务器备份恢复，不要手工拼接 JSONL。
