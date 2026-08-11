#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

./scripts/regression.sh >/dev/null

AGENT_ID="${JARVIS_AGENT_ID:-default}"
WORKSPACE="/app/working/workspaces/${AGENT_ID}"
TOOL="$WORKSPACE/skills/jarvis-project/scripts/projectctl.py"
RELEASE="$(git rev-parse --short=7 HEAD)"

projectctl() {
  docker compose exec -T jarvis python "$TOOL" --workspace "$WORKSPACE" "$@"
}

projectctl init --project jarvis --name Jarvis

projectctl record --project jarvis --kind decision --status noted \
  --title "Jarvis V1运行架构" \
  --content "QwenPaw负责钉钉渠道和Agent运行，Codex通过ChatGPT订阅提供推理与执行，私有知识、记忆和项目状态保存在服务器工作区。" \
  --source "Jarvis V1架构基线"

projectctl record --project jarvis --kind milestone --status done \
  --title "旧知识与记忆完成迁移" \
  --content "旧codex-dingtalk与codex-workspace知识已复制、校验并安装到Jarvis工作区，原始归档继续保留。" \
  --source "2026-08-10服务器迁移验收"

projectctl record --project jarvis --kind milestone --status done \
  --title "Codex订阅后端与最终答复模式启用" \
  --content "Jarvis已使用ChatGPT订阅认证的Codex后端，钉钉仅输出每轮最终答复，容器健康检查通过。" \
  --source "2026-08-10服务器运行验收"

projectctl record --project jarvis --kind milestone --status done \
  --title "五项Jarvis工作流能力启用" \
  --content "材料归档、管理写作、项目跟踪、持续记忆和钉钉表达五项Skill均已安装、启用并通过完整性检查。" \
  --source "服务器工作流检查"

projectctl record --project jarvis --kind milestone --status done \
  --title "当前版本技术回归通过" \
  --content "代码单元测试、工作流完整性、Codex后端、最终答复补丁和容器健康检查均已通过；钉钉端继续保留人工验收。" \
  --source "${RELEASE}技术回归报告"

projectctl record --project jarvis --kind update --status noted \
  --title "表达层与记忆自动收口上线" \
  --content "钉钉复杂答复已支持语义Emoji和分区呈现；每轮稳定记忆候选支持分级、整批校验、去重和冲突保护。" \
  --source "${RELEASE}钉钉与记忆验收"

projectctl record --project jarvis --kind action --status open \
  --title "完成当前版本端到端回归" \
  --content "覆盖记忆、材料归档、项目登记、表达呈现、重启连续性和钉钉人工验收，并形成稳定版本基线。" \
  --source "Jarvis V1收口计划"

projectctl record --project jarvis --kind risk --status open \
  --title "旧Current Turn Guard尚未完成等价性验证" \
  --content "旧codex-dingtalk的中断恢复与WAITING_RESTART机制属于历史能力，当前QwenPaw运行链路尚未完成等价回归，不能视为已验收。" \
  --source "2026-08-11新旧架构核对"

echo "Seeded the verified Jarvis V1 project baseline for release ${RELEASE}."
