---
name: jarvis-project
description: "当用户讨论持续项目的现状、决策、责任人、行动项、风险、里程碑、上次会议结论或要求把材料纳入某项目时使用；维护可追溯的项目状态和行动闭环。"
metadata:
  qwenpaw:
    emoji: "🗂️"
---

# Jarvis 项目状态与行动闭环

## 目标

把分散在聊天和材料中的项目事实整理为四类可持续状态：

- 已确认决策；
- 行动项及责任人；
- 里程碑和进展；
- 风险与待解决问题。

不要把模型建议、头脑风暴和未经确认的推断登记成既定决策。

## 用户体验

- 查询项目时直接给“当前判断、关键进展、待办和风险”，不展示内部文件结构。
- 记录状态时保持静默；用户明确要求纳入项目时，最终只需自然确认。
- 不输出项目条目 ID、账本路径、命令或内部分类，除非用户明确排查系统。

## 项目工具

```bash
PROJECT_TOOL="skills/jarvis-project/scripts/projectctl.py"
```

首次遇到持续项目时初始化：

```bash
python "$PROJECT_TOOL" --workspace . init \
  --project enterprise-architecture \
  --name '企业架构'
```

项目键使用小写字母、数字和连字符。项目中文名称仅作为展示名称。

## 登记规则

登记前必须先确认目标项目。`jarvis` 只用于 Jarvis 智能体自身的架构、部署、能力、
缺陷和版本事项，不能作为不明确材料的默认项目；万佛、用增、用户增长、公域经营
材料应进入 `wanfuo`。未知项目使用 `inbox` 或向用户确认。

即使用户明确给出项目名称，如果材料标题、正文和既有项目状态持续指向另一个已知
项目，也不要静默登记。先用一句话指出“材料内容与目标项目可能不一致”，只确认
一次目标项目；确认后再写入。测试和验收材料必须与目标项目主题一致，不能用业务
材料测试 Jarvis 项目台账。

会议或用户明确陈述形成事实后，按最小独立条目登记：

```bash
python "$PROJECT_TOOL" --workspace . record \
  --project enterprise-architecture \
  --kind decision \
  --title '简洁标题' \
  --content '已经确认、可独立理解的完整结论' \
  --source '钉钉当前会话；用户明确确认'
```

行动项需要尽量写清责任人和时间；原文没有就留空，不猜测：

```bash
python "$PROJECT_TOOL" --workspace . record \
  --project enterprise-architecture \
  --kind action \
  --title '行动项标题' \
  --content '要完成的结果' \
  --owner '原文明确的责任人' \
  --due 'YYYY-MM-DD' \
  --source '会议材料'
```

类型包括 `decision`、`action`、`milestone`、`risk`、`update`。同一来源中内容完全
相同的条目会自动去重。

## 完成与变更

只有用户或材料明确确认状态变化时才更新：

```bash
python "$PROJECT_TOOL" --workspace . change ITEM_ID \
  --status done \
  --note '完成依据或变化说明'
```

允许状态：`open`、`planned`、`active`、`blocked`、`done`、`cancelled`、`noted`。
变更使用追加式事件，不覆盖历史。

项目登记错误时使用迁移，不删除或直接改写账本：

```bash
python "$PROJECT_TOOL" --workspace . move ITEM_ID \
  --project jarvis \
  --to-project wanfuo \
  --to-name '万佛用户增长平台' \
  --reason '用户确认目标项目选择错误'
```

迁移会在目标项目创建关联条目，并在源项目保留 `moved` 历史。重复执行保持幂等；
迁出条目不再出现在源项目当前决策、行动和风险视图中。

## 与材料和记忆的边界

- 原始附件由 `jarvis-intake` 归档；
- 项目条目只保存提炼后的结论和来源说明，不复制全文；
- 跨项目长期偏好、个人事实和通用标准由 `jarvis-memory` 保存；
- 纪要、周报和汇报的表达由 `jarvis-management-writing` 负责。

## 查询

优先读取当前视图：

```bash
knowledge/projects/PROJECT_KEY/STATUS.md
knowledge/projects/PROJECT_KEY/DECISIONS.md
knowledge/projects/PROJECT_KEY/ACTIONS.md
knowledge/projects/PROJECT_KEY/TIMELINE.md
```

这些路径只用于内部检索，普通回复不展示。需要技术校验时：

```bash
python "$PROJECT_TOOL" --workspace . verify --project PROJECT_KEY --rebuild
python "$PROJECT_TOOL" --workspace . status --project PROJECT_KEY
```
