#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from core.paths import DATA_DIR, PROJECT_ROOT, WORKSPACE_ROOT

HISTORY_FILE = DATA_DIR / "recent_history.json"
SUMMARY_FILE = DATA_DIR / "rolling_summary.md"
STATE_FILE = DATA_DIR / "rolling_summary_state.json"

CODEX_BIN = os.getenv("CODEX_BIN", "codex")
WORK_DIR = os.getenv("CODEX_WORKDIR", str(WORKSPACE_ROOT))

PROMPT_MAX_ITEMS = int(os.getenv("RECENT_HISTORY_PROMPT_MAX", "20"))
SUMMARY_MAX_CHARS = int(os.getenv("ROLLING_SUMMARY_MAX_CHARS", "8000"))
ITEM_TEXT_MAX = int(os.getenv("ROLLING_SUMMARY_ITEM_TEXT_MAX", "1500"))
TIMEOUT = int(os.getenv("ROLLING_SUMMARY_TIMEOUT", "420"))


def read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_text_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except Exception:
            pass


def write_json_atomic(path: Path, data):
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def item_id(item):
    raw = json.dumps(
        {
            "role": item.get("role", ""),
            "time": item.get("time", ""),
            "text": item.get("text") or item.get("content") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_summary():
    if not SUMMARY_FILE.exists():
        return ""
    text = SUMMARY_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    if len(text) > SUMMARY_MAX_CHARS:
        return text[-SUMMARY_MAX_CHARS:]
    return text


def collect_unsummarized(history, state, prompt_max_items, force=False):
    bundles = []
    new_ids_by_session = {}

    for session_key, items in history.items():
        if not isinstance(items, list):
            continue

        old_items = items[:-prompt_max_items] if len(items) > prompt_max_items else []
        if not old_items:
            continue

        session_state = state.setdefault(session_key, {})
        summarized_ids = set(session_state.get("summarized_ids", []))

        selected = []
        selected_ids = []

        for item in old_items:
            iid = item_id(item)
            if force or iid not in summarized_ids:
                selected.append(item)
                selected_ids.append(iid)

        if selected:
            bundles.append({
                "session_key": session_key,
                "items": selected,
            })
            new_ids_by_session[session_key] = selected_ids

    return bundles, new_ids_by_session


def format_items(bundles):
    lines = []
    for bundle in bundles:
        lines.append(f"### 会话：{bundle['session_key']}")
        for item in bundle["items"]:
            role = item.get("role", "user")
            role_name = "用户" if role == "user" else "助手"
            time = item.get("time", "")
            text = (item.get("text") or item.get("content") or "").strip()
            if len(text) > ITEM_TEXT_MAX:
                text = text[:ITEM_TEXT_MAX] + "\n...[本条对话已截断]"
            lines.append(f"- {time} {role_name}：{text}")
        lines.append("")
    return "\n".join(lines).strip()


def clean_summary_output(text):
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def run_codex_summary(existing_summary, new_dialogue):
    prompt = f"""
你是钉钉 Codex 助手的“滚动记忆整理器”。

任务：把【已有滚动摘要】和【新增旧对话】合并，生成一份更新后的 rolling_summary.md。

注意：
1. 这不是聊天回复，而是给后续 prompt 使用的长期滚动上下文。
2. 保留用户偏好、项目状态、文件路径、关键决策、已验证结论、错误教训、未完成事项。
3. 删除寒暄、重复、短期无价值 ping、无意义确认。
4. 对企业架构、增长黑客、AI 工程、钉钉 Codex 助手建设等学习脉络，要保留成体系的上下文。
5. 不要编造；只基于已有摘要和新增对话归纳。
6. 输出 Markdown 正文，不要代码块，不要解释“我做了什么”。
7. 总长度控制在 8000 字以内，优先保留高价值信息。

建议结构：
# Rolling Summary

## 一、助手当前能力与系统状态
## 二、用户长期偏好与工作方式
## 三、钉钉 Codex 助手建设进展
## 四、企业架构 / 增长黑客 / AI 工程学习脉络
## 五、重要文件、路径与稳定版本
## 六、近期问题、教训与待办

【已有滚动摘要】
{existing_summary or "无"}

【新增旧对话】
{new_dialogue}
""".strip()

    cmd = [
        CODEX_BIN,
        "exec",
        "--sandbox",
        "workspace-write",
        "--add-dir",
        str(PROJECT_ROOT),
        "--skip-git-repo-check",
        "--cd",
        WORK_DIR,
        "-",
    ]

    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=TIMEOUT,
    )

    output = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "Codex summary failed")[-4000:])

    output = clean_summary_output(output)
    if not output:
        raise RuntimeError("Codex summary output is empty")

    if len(output) > SUMMARY_MAX_CHARS:
        output = output[-SUMMARY_MAX_CHARS:]

    return output


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="重新汇总所有超过 recent window 的旧消息")
    ap.add_argument("--max-prompt-items", type=int, default=PROMPT_MAX_ITEMS)
    args = ap.parse_args()

    history = read_json(HISTORY_FILE, {})
    state = read_json(STATE_FILE, {})

    bundles, new_ids_by_session = collect_unsummarized(
        history,
        state,
        prompt_max_items=args.max_prompt_items,
        force=args.force,
    )

    total = sum(len(b["items"]) for b in bundles)
    print(f"rolling summary: sessions={len(bundles)} new_old_items={total}")

    if total == 0:
        return

    new_dialogue = format_items(bundles)

    if args.dry_run:
        print(new_dialogue[:3000])
        return

    existing_summary = load_summary()
    updated_summary = run_codex_summary(existing_summary, new_dialogue)

    header = f"<!-- updated_at: {datetime.now().isoformat(timespec='seconds')} -->\n\n"
    write_text_atomic(SUMMARY_FILE, header + updated_summary.strip() + "\n")

    for session_key, ids in new_ids_by_session.items():
        session_state = state.setdefault(session_key, {})
        old_ids = session_state.get("summarized_ids", [])
        merged = old_ids + ids
        session_state["summarized_ids"] = merged[-2000:]
        session_state["last_updated"] = datetime.now().isoformat(timespec="seconds")

    write_json_atomic(STATE_FILE, state)

    print(f"updated: {SUMMARY_FILE}")
    print(f"state: {STATE_FILE}")


if __name__ == "__main__":
    main()
