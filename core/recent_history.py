import json
import os
import tempfile
from datetime import datetime
from core.paths import DATA_DIR


HISTORY_FILE = DATA_DIR / "recent_history.json"

# 落盘保留多少条；不是每次都塞进 prompt
STORE_MAX_ITEMS = int(os.getenv("RECENT_HISTORY_STORE_MAX", "80"))

# 每次进入 prompt 的最近上下文条数
PROMPT_MAX_ITEMS = int(os.getenv("RECENT_HISTORY_PROMPT_MAX", "20"))

# 单条最多保存多少字符，避免 assistant 长回答把 json 撑爆
ITEM_MAX_CHARS = int(os.getenv("RECENT_HISTORY_ITEM_MAX_CHARS", "3000"))


def _truncate(text: str, limit: int = ITEM_MAX_CHARS) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[本条历史已截断]"


def _safe_key(session_id: str) -> str:
    key = str(session_id or "default").strip()
    return key or "default"


def load_all_history():
    if not HISTORY_FILE.exists():
        return {}

    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_all_history(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix="recent_history_",
        suffix=".json",
        dir=str(DATA_DIR),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp_name, HISTORY_FILE)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except Exception:
            pass


def get_recent_items(session_id: str, max_items: int = PROMPT_MAX_ITEMS):
    key = _safe_key(session_id)
    data = load_all_history()
    items = data.get(key, [])[-max_items:]

    result = []
    for item in items:
        role = item.get("role") or "user"
        text = item.get("text") or item.get("content") or ""
        text = str(text).strip()
        if not text:
            continue

        result.append({
            "role": "assistant" if role == "assistant" else "user",
            "text": text,
            "time": item.get("time", ""),
        })

    return result


def get_recent_complete_turns(session_id: str, max_turns: int = 4):
    """Return only the latest complete user -> assistant turns without changing stored history."""
    key = _safe_key(session_id)
    data = load_all_history()
    turns = []
    pending_user = None

    for item in data.get(key, []):
        role = "assistant" if item.get("role") == "assistant" else "user"
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        normalized = {"role": role, "text": text, "time": item.get("time", "")}
        if role == "user":
            pending_user = normalized
        elif pending_user is not None:
            turns.append((pending_user, normalized))
            pending_user = None

    limit = max(0, int(max_turns))
    if limit == 0:
        return []

    result = []
    for user_item, assistant_item in turns[-limit:]:
        result.extend((user_item, assistant_item))
    return result


def append_history(session_id: str, role: str, content: str):
    key = _safe_key(session_id)
    role = "assistant" if role == "assistant" else "user"
    content = _truncate(content)

    if not content:
        return

    data = load_all_history()
    data.setdefault(key, [])

    data[key].append({
        "role": role,
        "text": content,
        "time": datetime.now().isoformat(timespec="seconds"),
    })

    data[key] = data[key][-STORE_MAX_ITEMS:]
    save_all_history(data)
