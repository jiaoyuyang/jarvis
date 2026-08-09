import json
import os
import re
import subprocess
from pathlib import Path

from core.paths import WORKSPACE_ROOT


CODEX_BIN = os.getenv("CODEX_BIN", "codex")
WORK_DIR = os.getenv("CODEX_WORKDIR", str(WORKSPACE_ROOT))
TIMEOUT = int(os.getenv("CODEX_TIMEOUT", "300"))


def _extract_json(text: str):
    text = (text or "").strip()
    if not text:
        raise ValueError("empty codex output")

    try:
        return json.loads(text)
    except Exception:
        pass

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("no json object found")
    return json.loads(m.group(0))


def extract_hybrid_layout(image_path: str, user_text: str = ""):
    out_dir = Path(WORK_DIR) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    msg_file = out_dir / "hybrid_layout_last_message.json.txt"

    prompt = f"""
你在执行“图片转PPT 2.8 混合复刻模式”的版面抽取任务。

目标不是全量重建，而是抽取“关键可编辑层”，用于 PPT 上叠加编辑。
请阅读图片，只输出 JSON，不要 markdown，不要解释。

输出格式：
{{
  "page_title": "页面主标题",
  "subtitle": "页面副标题，没有就留空",
  "footer_text": "底部橙色/结论栏文字，没有就留空",
  "overlays": [
    {{"type": "title", "text": "页面主标题", "bbox": [x, y, w, h]}},
    {{"type": "subtitle", "text": "页面副标题", "bbox": [x, y, w, h]}},
    {{"type": "section", "text": "一级分区标题", "bbox": [x, y, w, h]}},
    {{"type": "card_title", "text": "卡片标题", "bbox": [x, y, w, h]}},
    {{"type": "footer", "text": "底部结论栏", "bbox": [x, y, w, h]}}
  ]
}}

约束：
1. bbox 使用相对整张图片的归一化坐标，范围 0 到 1。
2. 只抽取关键层：标题、副标题、一级分区标题、卡片标题、底部结论栏。
3. 不要抽取大段正文小字。
4. overlays 最多 12 个。
5. 标题命名尽量准确，适配商务 PPT。
6. 如果用户有补充要求，要尽量体现在标题/区域抽取中。

用户补充要求：
{user_text or "无"}
""".strip()

    cmd = [
        CODEX_BIN,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        WORK_DIR,
        "-i",
        image_path,
        "-o",
        str(msg_file),
        prompt,
    ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )

    raw = ""
    if msg_file.exists():
        raw = msg_file.read_text(encoding="utf-8", errors="ignore")

    if proc.returncode != 0 and not raw.strip():
        raise RuntimeError(
            "codex layout extract failed:\n"
            + (proc.stderr or proc.stdout or "").strip()[-2000:]
        )

    data = _extract_json(raw)

    if "overlays" not in data or not isinstance(data.get("overlays"), list):
        data["overlays"] = []

    data.setdefault("page_title", "")
    data.setdefault("subtitle", "")
    data.setdefault("footer_text", "")

    return data
