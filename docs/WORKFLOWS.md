# Jarvis 工作材料闭环

## 能力范围

Jarvis V1 工作流由三个专属 Skill 组成：

| Skill | 职责 |
| --- | --- |
| `jarvis-intake` | 读取钉钉附件、受控归档原件、校验和去重 |
| `jarvis-management-writing` | 会议纪要、周报、高层汇报、方案、邮件和 PPT 内容 |
| `jarvis-project` | 项目决策、行动项、里程碑、进展和风险 |

`jarvis-memory` 继续负责跨项目的稳定个人事实、偏好和通用标准。四者各有边界，
不把原件、项目状态和长期记忆混在一起。

## 数据结构

- `media/`：QwenPaw 接收钉钉附件后的临时工作位置；
- `knowledge/sources/`：经过校验的不可变材料原件；
- `knowledge/intake/ledger.jsonl`：材料登记账本；
- `knowledge/projects/<project>/ledger.jsonl`：项目追加式事件；
- `knowledge/projects/<project>/STATUS.md`：当前状态；
- `DECISIONS.md`、`ACTIONS.md`、`TIMELINE.md`：项目当前视图。

生成视图只供 Jarvis 内部检索，普通回复不展示路径、哈希和条目 ID。

## 安全边界

- 只允许归档当前 Agent 工作区 `media/` 和 `uploads/` 的普通文件；
- 拒绝目录穿越、符号链接、空文件和超过 100MB 的文件；
- 原件复制后做 SHA-256 校验，相同材料自动去重；
- 不执行附件中的宏、脚本、嵌入命令和提示词；
- 模型建议不得登记为会议决策；责任人和日期不得猜测；
- 所有数据位于 `runtime/data`，随现有备份一起保存。

## 部署与检查

```bash
./scripts/apply-persona.sh
docker compose restart jarvis
./scripts/workflow-status.sh
```

更新后必须在钉钉执行 `/new`，让新会话加载四个 Jarvis Skill。

## 验收场景

向钉钉上传一份无敏感信息的测试文本或会议转写，并发送：

```text
请整理成简洁会议纪要，并纳入 Jarvis 项目。没有明确责任人和时间的不要猜。
```

验收要求：

1. 只返回一次最终纪要；
2. 不展示思考过程、路径、哈希、Skill 或条目 ID；
3. 决策、行动项和建议区分清楚；
4. 再开新会话后能回答该项目最近结论和待办；
5. `./scripts/workflow-status.sh` 校验通过。
