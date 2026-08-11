#!/usr/bin/env python3
"""Patch QwenPaw DingTalk turns with durable restart closure.

QwenPaw adds a ``Thinking`` reaction before processing a DingTalk request and
normally recalls it only after the turn completes.  A container restart loses
the in-memory task, leaving the reaction behind forever.  AI Card mode has a
card recovery store, but markdown mode and the incoming-message reaction have
no equivalent checkpoint.

This patch persists only the minimum delivery metadata for an in-flight turn.
After restart it closes the stale reaction and either reuses the recovered AI
Card notice or sends a concise interruption notification.  User prompts and
model output are deliberately not persisted in this checkpoint.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "# JARVIS_DINGTALK_TURN_RECOVERY_PATCH_V1"

INIT_ANCHOR = """        self._card_store = AICardPendingStore(
            cards_dir / "dingtalk-active-cards.json",
        )
        # Use workspace-specific media dir if workspace_dir is provided
"""

INIT_REPLACEMENT = f"""        self._card_store = AICardPendingStore(
            cards_dir / "dingtalk-active-cards.json",
        )
        {MARKER}
        self._inflight_turn_store_path = (
            cards_dir / "dingtalk-inflight-turns.json"
        )
        self._inflight_turns: Dict[str, Dict[str, Any]] = {{}}
        self._inflight_turns_lock = asyncio.Lock()
        # Use workspace-specific media dir if workspace_dir is provided
"""

HELPER_ANCHOR = """    def _session_webhook_store_path(self) -> Path:
"""

HELPER_REPLACEMENT = """    def _load_inflight_turns_from_disk(self) -> Dict[str, Dict[str, Any]]:
        path = self._inflight_turn_store_path
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            turns = data.get("turns") if isinstance(data, dict) else []
            if not isinstance(turns, list):
                return {}
            return {
                str(item.get("message_id")): item
                for item in turns
                if isinstance(item, dict) and item.get("message_id")
            }
        except Exception:
            logger.exception("dingtalk in-flight turn store load failed")
            return {}

    def _save_inflight_turns_to_disk(self) -> None:
        path = self._inflight_turn_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": int(time.time() * 1000),
            "turns": list(self._inflight_turns.values()),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    async def _mark_inflight_turn(self, request: "AgentRequest") -> None:
        meta = getattr(request, "channel_meta", None) or {}
        message_id = str(meta.get("message_id") or "")
        conversation_id = str(meta.get("conversation_id") or "")
        if not message_id or not conversation_id:
            return
        record = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "conversation_type": str(meta.get("conversation_type") or ""),
            "sender_staff_id": str(meta.get("sender_staff_id") or ""),
            "session_id": str(getattr(request, "session_id", "") or ""),
            "to_handle": self.get_to_handle_from_request(request),
            "created_at": int(time.time() * 1000),
        }
        async with self._inflight_turns_lock:
            if not self._inflight_turns:
                self._inflight_turns = self._load_inflight_turns_from_disk()
            self._inflight_turns[message_id] = record
            self._save_inflight_turns_to_disk()

    async def _complete_inflight_turn(self, message_id: str) -> None:
        if not message_id:
            return
        async with self._inflight_turns_lock:
            if not self._inflight_turns:
                self._inflight_turns = self._load_inflight_turns_from_disk()
            if self._inflight_turns.pop(message_id, None) is not None:
                self._save_inflight_turns_to_disk()

    async def _recover_inflight_turns(
        self,
        card_notified_conversations: set[str],
    ) -> None:
        async with self._inflight_turns_lock:
            self._inflight_turns = self._load_inflight_turns_from_disk()
            records = list(self._inflight_turns.values())
        for record in records:
            message_id = str(record.get("message_id") or "")
            conversation_id = str(record.get("conversation_id") or "")
            try:
                await self._send_emotion(
                    message_id,
                    conversation_id,
                    "🤔Thinking",
                    recall=True,
                )
                await self._send_emotion(
                    message_id,
                    conversation_id,
                    "☹️Error",
                )
                if conversation_id not in card_notified_conversations:
                    meta = {
                        "conversation_id": conversation_id,
                        "conversation_type": str(
                            record.get("conversation_type") or ""
                        ),
                        "sender_staff_id": str(
                            record.get("sender_staff_id") or ""
                        ),
                        "_api_send": True,
                    }
                    await self.send(
                        str(record.get("to_handle") or ""),
                        "⚠️ 上一条任务因 Jarvis 重启而中断，"
                        "已结束等待状态。请重新发送原问题。",
                        meta,
                    )
            except Exception:
                logger.exception(
                    "dingtalk in-flight turn recovery failed: message_id=%s",
                    message_id,
                )
                continue
            await self._complete_inflight_turn(message_id)

    def _session_webhook_store_path(self) -> Path:
"""

CHECKPOINT_ANCHOR = """        # Add "processing" reaction to user's incoming message
"""

CHECKPOINT_REPLACEMENT = (
    "        # Persist minimal delivery metadata before exposing Thinking "
    "state.\n"
    "        await self._mark_inflight_turn(request)\n"
    "\n"
    '        # Add "processing" reaction to user\'s incoming message\n'
)

ERROR_CLEAR_ANCHOR = """        # Release dedup msg_id so future retries are accepted
        msg_ids = meta.get("_message_ids")
"""

ERROR_CLEAR_REPLACEMENT = """        await self._complete_inflight_turn(incoming_msg_id)

        # Release dedup msg_id so future retries are accepted
        msg_ids = meta.get("_message_ids")
"""

COMPLETE_CLEAR_ANCHOR = (
    "        # Release dedup msg_id so future messages with same id are "
    "accepted\n"
    '        msg_ids = (send_meta or {}).get("_message_ids")\n'
)

COMPLETE_CLEAR_REPLACEMENT = """        await self._complete_inflight_turn(incoming_msg_id)

        # Release dedup msg_id so future messages with same id are accepted
        msg_ids = (send_meta or {}).get("_message_ids")
"""

START_ANCHOR = """        await self._recover_active_cards()
"""

START_REPLACEMENT = """        card_notified_conversations = await self._recover_active_cards()
        await self._recover_inflight_turns(card_notified_conversations)
"""

CARD_RECOVERY_HEAD_ANCHOR = """    async def _recover_active_cards(self) -> None:
        if not self._ai_card_enabled() or self._card_sdk is None:
            return
        records = self._card_store.load()
        if not records:
            return
        token = await self._get_access_token()
"""

CARD_RECOVERY_HEAD_REPLACEMENT = """    async def _recover_active_cards(self) -> set[str]:
        recovered_conversations: set[str] = set()
        if not self._ai_card_enabled() or self._card_sdk is None:
            return recovered_conversations
        records = self._card_store.load()
        if not records:
            return recovered_conversations
        token = await self._get_access_token()
"""

CARD_RECOVERY_SUCCESS_ANCHOR = """                await self._stream_ai_card(
                    card,
                    AI_CARD_RECOVERY_FINAL_TEXT,
                    finalize=True,
                )
            except Exception:
"""

CARD_RECOVERY_SUCCESS_REPLACEMENT = """                await self._stream_ai_card(
                    card,
                    AI_CARD_RECOVERY_FINAL_TEXT,
                    finalize=True,
                )
                recovered_conversations.add(conversation_id)
            except Exception:
"""

CARD_RECOVERY_RETURN_ANCHOR = """                await self._mark_card_failed(conversation_id)

    async def send(
"""

CARD_RECOVERY_RETURN_REPLACEMENT = """                await self._mark_card_failed(conversation_id)
        return recovered_conversations

    async def send(
"""


REPLACEMENTS = (
    (INIT_ANCHOR, INIT_REPLACEMENT),
    (HELPER_ANCHOR, HELPER_REPLACEMENT),
    (CHECKPOINT_ANCHOR, CHECKPOINT_REPLACEMENT),
    (ERROR_CLEAR_ANCHOR, ERROR_CLEAR_REPLACEMENT),
    (COMPLETE_CLEAR_ANCHOR, COMPLETE_CLEAR_REPLACEMENT),
    (START_ANCHOR, START_REPLACEMENT),
    (CARD_RECOVERY_HEAD_ANCHOR, CARD_RECOVERY_HEAD_REPLACEMENT),
    (CARD_RECOVERY_SUCCESS_ANCHOR, CARD_RECOVERY_SUCCESS_REPLACEMENT),
    (CARD_RECOVERY_RETURN_ANCHOR, CARD_RECOVERY_RETURN_REPLACEMENT),
)


def resolve_channel_path() -> Path:
    spec = importlib.util.find_spec("qwenpaw.app.channels.dingtalk.channel")
    if spec is None or not spec.origin:
        raise SystemExit("QwenPaw DingTalk channel was not found")
    return Path(spec.origin)


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"Jarvis DingTalk turn recovery patch already present: {path}")
        return

    for anchor, _replacement in REPLACEMENTS:
        if source.count(anchor) != 1:
            raise SystemExit(
                "QwenPaw DingTalk recovery anchor did not match exactly once: "
                + anchor.splitlines()[0]
            )
    for anchor, replacement in REPLACEMENTS:
        source = source.replace(anchor, replacement)
    path.write_text(source, encoding="utf-8")
    print(f"Applied Jarvis DingTalk turn recovery patch: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        type=Path,
        help="Channel path for tests; defaults to the installed QwenPaw module",
    )
    args = parser.parse_args()
    patch(args.path or resolve_channel_path())


if __name__ == "__main__":
    main()
