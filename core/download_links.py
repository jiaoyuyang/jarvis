import json
import os
import re
import secrets
import time
from pathlib import Path

from core.paths import DATA_DIR, OUTPUT_DIR as CONFIGURED_OUTPUT_DIR

TOKEN_FILE = DATA_DIR / "download_tokens.json"
OUTPUT_DIR = CONFIGURED_OUTPUT_DIR.resolve()
DEFAULT_TTL_SECONDS = 24 * 3600


def _load_tokens():
    if not TOKEN_FILE.exists():
        return {}
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_tokens(tokens):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    TOKEN_FILE.chmod(0o600)


def _is_allowed_file(path):
    try:
        p = Path(path).resolve()
        return p.exists() and p.is_file() and str(p).startswith(str(OUTPUT_DIR) + "/")
    except Exception:
        return False


def create_download_link(file_path, ttl_seconds=DEFAULT_TTL_SECONDS):
    if not _is_allowed_file(file_path):
        return None

    base_url = (os.getenv("DOWNLOAD_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        return None

    tokens = _load_tokens()

    now = int(time.time())
    # 清理过期 token
    tokens = {
        k: v for k, v in tokens.items()
        if int(v.get("expires_at", 0)) > now
    }

    token = secrets.token_urlsafe(24)
    tokens[token] = {
        "path": str(Path(file_path).resolve()),
        "created_at": now,
        "expires_at": now + ttl_seconds,
    }

    _save_tokens(tokens)

    return f"{base_url}/d/{token}"


def append_download_links(text):
    text = text or ""

    pattern = r"/[^\s\)\]\n\r]+?\.pptx"
    paths = []
    for m in re.finditer(pattern, text):
        p = m.group(0).strip().strip("。,.，")
        if p not in paths:
            paths.append(p)

    links = []
    for p in paths:
        link = create_download_link(p)
        if link:
            links.append((p, link))

    if not links:
        return text

    extra = ["", "文件下载："]
    for p, link in links:
        name = Path(p).name
        extra.append(f"- {name}：{link}")

    extra.append("")
    extra.append("链接有效期：24小时。")

    return text.rstrip() + "\n\n" + "\n".join(extra)
