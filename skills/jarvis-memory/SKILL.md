---
name: jarvis-memory
description: "当问题涉及焦书记的身份、偏好、历史决策、企业架构、用增平台、万佛、数据流通、PPT规范或其他既有项目知识时，先检索本地知识库再回答；当用户明确要求记住、更正或删除长期信息时，也使用本技能。"
metadata:
  qwenpaw:
    emoji: "🧠"
    requires:
      bins:
        - rg
---

# Jarvis 本地知识与持续记忆

本技能把服务器工作区中的 Markdown 文档作为 Jarvis 的私有知识库。知识
不会因为 ChatGPT OAuth 登录而自动同步，也不要假设 ChatGPT 网页版记忆
已经存在于当前环境。

## 回答方式

- 常规检索不要先发送“我会检索”“正在使用技能”等过程性消息，直接检索并
  给出最终答案。
- 默认把本地知识当作 Jarvis 自身记忆自然使用，不向用户说明底层检索和存储
  过程，不展示知识库、Skill、文件路径、行号、记忆 ID 或内部分类。
- 优先保证响应速度。整理区证据足够时立即停止，不为重复佐证扫描原始归档。
- 最多读取 3 个最相关文件；来源不足时明确说明，不进行无边界搜索。
- 检索、写入和整理过程保持静默，每个请求只发送一次最终答复。

## 什么时候必须检索

回答以下问题前，先检索再作答：

- 用户身份、称呼、偏好、工作方式和表达风格；
- 历史项目、会议、决策、制度、架构和实施计划；
- “以前说过什么”“上次怎么定的”“按我的习惯”等连续性问题；
- 企业架构、数据架构、AI 架构、用增平台、万佛和数据流通；
- PPT、周报、纪要、邮件和管理汇报的既定标准。

## 记忆分层

- `memory/inbox/YYYY-MM-DD.md`：日常会话产生的候选记忆，保留时间、来源和初始状态。
- `memory/ledger.jsonl`：追加式审计账本，是新记忆的事实源，不允许手工修改。
- `memory/state.json`：根据账本生成的机器状态，用于去重和冲突判断。
- `memory/curated/`：根据账本生成的当前有效长期记忆，回答时优先检索。
- `knowledge/`、`PROFILE.md` 和迁移前记忆：历史知识层，继续保留和检索，不自动改写。

`memory/curated/` 和 `memory/state.json` 都可以通过账本重建。禁止直接编辑这些
生成文件；所有新增、更正、晋升和停用都必须使用本技能附带的 `memoryctl.py`。

## 分层检索步骤

1. 当前工作目录应为 Jarvis workspace。
2. 从问题提取 2–5 个有区分度的关键词和同义词，避免单独使用“通知”
   “搜索”“项目”等高频词。
3. 首先查当前有效长期记忆、用户资料、私有核心知识和整理后的知识，输出最多 40 条命中：

   ```bash
   rg -n -i --glob '*.md' --glob '*.txt' -- \
     '关键词1|关键词2|同义词' memory/curated PROFILE.md \
     knowledge/private/core knowledge/imports memory/inbox digest \
     | head -40
   ```

4. 命中后优先读取 `knowledge/private/core/` 中最相关的结构化文件，再读取其他来源的必要上下文。若已足以回答，立即结束检索。
5. 仅在整理区没有关键事实，或用户明确要求核对原始材料时，定向查询归档。
   排除代码备份、变更基线、构建输出和第三方技能，最长运行 20 秒：

   ```bash
   timeout 20s rg -n -i --glob '*.md' --glob '*.txt' \
     --glob '!**/agent_backups/**' \
     --glob '!**/change_requests/**' \
     --glob '!**/outputs/**' \
     --glob '!**/persona-skills/**' -- \
     '有区分度的关键词1|关键词2' knowledge/archive \
     | head -60
   ```

6. 如果定向归档仍未命中，就回答“本地知识库暂未检索到”，不要继续扩大
   到整个文件系统，也不要把模型推断冒充历史记忆。
7. 多个来源冲突时，优先采用日期更新、用户明确确认、来源更直接的记录；
   无法判断时把冲突告诉用户。
8. 默认回答不得出现 `【知识库：...】`、工作区路径、文件名、行号、记忆 ID、
   “根据本地检索”等技术性来源标记。像熟悉用户的长期助理一样直接回答。
9. 只有用户明确要求“给出来源”“核对依据”“为什么记得”或正在排查记忆系统时，
   才说明来源。业务场景优先使用“你此前确认的偏好”“之前的项目材料”等自然
   语言；只有技术排障明确需要时才给工作区相对路径和行号。
10. 多个来源冲突时，可以自然说明“此前记录存在两个版本”，并请求用户确认；
    仍不要主动暴露底层文件结构。

## 每轮静默回写

完成一次有实质内容的会话后，判断是否产生了以下候选：

- 用户明确陈述的稳定事实或偏好；
- 已形成结论的项目决策、责任边界、里程碑或工作标准；
- 用户对既有事实的明确纠正；
- 可在多项任务中复用的方法和规则。

有候选时，在形成最终答复后、发送答复前静默写入；没有候选时不调用工具、不创建
空记录。不要保存整段聊天原文、模型最终答复或重复的历史事实，只保存可以脱离当前
对话独立理解的简洁结论。自动回写不向钉钉发送进度消息。

工具位置：

```bash
MEMORY_TOOL="skills/jarvis-memory/scripts/memoryctl.py"
```

一轮有多个候选时，优先使用单次收口命令。JSON 只通过标准输入传入，不能把用户
原文拼接成可执行 Shell：

```bash
python "$MEMORY_TOOL" --workspace . close-turn <<'JARVIS_MEMORY_JSON'
{
  "source": "钉钉当前会话；用户明确陈述",
  "candidates": [
    {
      "mode": "confirmed",
      "type": "preference",
      "category": "preferences",
      "key": "preference.example.concise_output",
      "content": "用户偏好简洁且结论先行的答复"
    }
  ]
}
JARVIS_MEMORY_JSON
```

`mode` 的语义必须严格区分：

- `remember`：用户明确要求“记住”“以后都这样”，直接形成有效长期记忆；
- `confirmed`：用户明确陈述的稳定内容，进入已确认候选层；
- `pending`：模型归纳且仍需核对，只进入待确认候选层。

一次性格式调整、临时任务、寒暄、模型建议和未形成结论的讨论不得作为候选。每轮
最多 8 条；工具会统一做敏感信息拒绝、去重、冲突检测和追加式记录。只需单条时也
可继续使用 `capture`：

```bash
python "$MEMORY_TOOL" --workspace . capture \
  --type decision \
  --category projects \
  --key project.example.delivery_date \
  --content '已经明确并可独立理解的简洁结论' \
  --source '钉钉当前会话；用户明确陈述' \
  --confirmed
```

只有模型推断、仍需用户核对的候选不要加 `--confirmed`。临时任务、寒暄、一次性
格式要求、未经确认的判断和敏感信息不进入记忆。

## 用户明确要求记住

用户明确说“记住这个”、确认稳定规则或要求形成长期记忆时，直接写入有效长期
记忆：

```bash
python "$MEMORY_TOOL" --workspace . remember \
  --type preference \
  --category preferences \
  --key preference.example.output_style \
  --content '用户明确确认的长期内容' \
  --source '钉钉当前会话；用户明确要求记住'
```

写入成功后，最终答复只需简短确认“已记住”；不要展示内部命令、知识库名称、
文件路径、记忆 ID、分类或账本内容。

## 稳定记忆键和分类

每条记忆必须使用稳定、具体的英文 `key`，例如：

- `profile.preferred_name`
- `preference.ppt.font`
- `project.wanfuo.public_resources`
- `decision.jarvis.memory_policy`
- `standard.enterprise_architecture.naming`

不要使用 `project.wanfuo` 这类过宽的键；同一主题的独立事实应使用不同键。

类型限于 `fact`、`preference`、`decision`、`standard`、`todo`。分类限于
`profile`、`people`、`preferences`、`projects`、`decisions`、`standards`、
`other`。

## 去重、整理与晋升

- 工具会对相同 `key` 和相同内容做幂等去重。
- 用户要求“整理记忆”时，先查看候选：

  ```bash
  python "$MEMORY_TOOL" --workspace . list --status pending --limit 50
  ```

- 证据充分且稳定的候选可以晋升：

  ```bash
  python "$MEMORY_TOOL" --workspace . promote MEMORY_ID --category projects
  ```

- 每当待确认候选达到 20 条，也应在合适的空闲维护任务中进行一次去重和分类；
  不要为了整理记忆打断当前用户任务。

## 更正、冲突与停用

同一 `key` 已存在有效版本而内容不同时，工具会拒绝直接覆盖。先定位当前 ID，
再创建更正版本：

```bash
python "$MEMORY_TOOL" --workspace . list --status active --limit 50
python "$MEMORY_TOOL" --workspace . correct OLD_MEMORY_ID \
  --content '用户确认的新版本' \
  --source '钉钉当前会话；用户明确更正' \
  --reason '更正原因'
```

旧版本会标记为 `superseded` 并保留在账本中，当前检索视图只显示新版本。

用户确认某条记忆不再使用后，执行逻辑停用：

```bash
python "$MEMORY_TOOL" --workspace . retire MEMORY_ID --reason '用户确认停用'
```

物理删除账本、收件箱或原始知识属于不可逆数据操作，必须先运行服务器备份并再次
确认目标；本技能不提供物理删除命令。

## 校验与恢复

需要检查记忆状态或从账本重建视图时：

```bash
python "$MEMORY_TOOL" --workspace . status
python "$MEMORY_TOOL" --workspace . verify --rebuild
```

服务器 `scripts/backup.sh` 已覆盖整个 `runtime/data`，因此收件箱、账本、状态和
长期视图会被一并备份。

## 写入边界

只记录以下内容：

- 用户明确要求“记住”的内容；
- 用户明确确认的稳定偏好、个人事实和工作规则；
- 已经形成结论的重要决策、责任边界和后续行动；
- 在多项任务中可复用的方法和标准。

不要写入密码、Token、API Key、SSH Key、身份证号、银行卡号等敏感凭据。
不要把临时任务、未经确认的推断、长段聊天原文写成长期记忆。
传给命令行的内容必须作为数据参数安全引用，不得把用户原文拼接成可执行 Shell。
