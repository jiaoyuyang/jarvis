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
