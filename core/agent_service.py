"""Channel-neutral text Agent entry point used by the HTTP Gateway.

Phase 1 deliberately leaves ``bot.py`` untouched.  Both entry points reuse the
same prompt, knowledge retrieval and Codex executor modules, while this service
keeps HTTP history in its own file until the DingTalk path is migrated.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime

from agent.prompt_compressor import compress_prompt
from core.executor_pool import ExecutorPool
from core.memory_context import MemoryContext
from core.memory_retriever import MemoryRetriever
from core.paths import DATA_DIR


HISTORY_FILE = DATA_DIR / "gateway_recent_history.json"
ALLOWED_CHANNELS = frozenset({"dingtalk", "voice", "web"})
STORE_MAX_ITEMS = 80
PROMPT_MAX_TURNS = 4
ITEM_MAX_CHARS = 3000
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")


class AgentService:
    """Serve ordinary text turns with channel/user scoped durable history."""

    def __init__(self) -> None:
        self.pool = ExecutorPool(max_concurrency=1)
        self.memory_retriever = MemoryRetriever()
        self.memory_context_loader = MemoryContext()
        self._history_lock = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def session_id(channel: str, user: str) -> str:
        channel = str(channel or "").strip().lower()
        user = str(user or "").strip()
        if channel not in ALLOWED_CHANNELS:
            raise ValueError("channel must be one of: dingtalk, voice, web")
        if not _SAFE_COMPONENT.fullmatch(user):
            raise ValueError("user must be 1-128 characters: letters, digits, . _ @ -")
        return f"{channel}:{user}"

    async def chat(self, *, user: str, channel: str, message: str) -> str:
        message = str(message or "").strip()
        if not message:
            raise ValueError("message must not be empty")
        if len(message) > 12000:
            raise ValueError("message must not exceed 12000 characters")

        session_id = self.session_id(channel, user)
        session_lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with session_lock:
            history = await self._get_complete_turns(session_id)
            await self._append_history(session_id, "user", message)

            memory_files = self.memory_retriever.retrieve(message)
            memory_context = self.memory_context_loader.load(memory_files)
            prompt = compress_prompt(message, history, memory_context=memory_context)
            answer = await self.pool.run(prompt)
            await self._append_history(session_id, "assistant", answer)
            return answer

    async def _get_complete_turns(self, session_id: str) -> list[dict[str, str]]:
        async with self._history_lock:
            records = self._load_history().get(session_id, [])
        turns: list[tuple[dict[str, str], dict[str, str]]] = []
        pending_user: dict[str, str] | None = None
        for item in records:
            text = str(item.get("text") or "").strip()
            role = "assistant" if item.get("role") == "assistant" else "user"
            if not text:
                continue
            normalized = {"role": role, "text": text, "time": str(item.get("time") or "")}
            if role == "user":
                pending_user = normalized
            elif pending_user is not None:
                turns.append((pending_user, normalized))
                pending_user = None
        return [item for turn in turns[-PROMPT_MAX_TURNS:] for item in turn]

    async def _append_history(self, session_id: str, role: str, content: str) -> None:
        content = str(content or "").strip()
        if not content:
            return
        if len(content) > ITEM_MAX_CHARS:
            content = content[:ITEM_MAX_CHARS] + "\n...[本条历史已截断]"
        async with self._history_lock:
            data = self._load_history()
            records = data.setdefault(session_id, [])
            records.append({
                "role": "assistant" if role == "assistant" else "user",
                "text": content,
                "time": datetime.now().isoformat(timespec="seconds"),
            })
            data[session_id] = records[-STORE_MAX_ITEMS:]
            self._save_history(data)

    @staticmethod
    def _load_history() -> dict:
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _save_history(data: dict) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="gateway_history_", suffix=".json", dir=DATA_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, HISTORY_FILE)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
