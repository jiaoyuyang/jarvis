#!/usr/bin/env python3
"""Guarantee that DingTalk AI Cards leave their processing state.

QwenPaw updates a non-streaming AI Card with completed message text and only
marks the card final after the whole response loop exits.  If that final API
call fails, or the response loop is cancelled after a partial update, the
TaskTracker can already be idle while DingTalk still shows an animated,
unfinished card.  The stale card looks like a running task even though
``/stop`` correctly reports that no task is active.

This patch adds bounded retries for final card updates, a plain-message
delivery fallback, explicit error-path closure, and cancellation cleanup based
on Jarvis's minimal in-flight turn checkpoint.  No prompt or model output is
persisted by this guard.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "# JARVIS_DINGTALK_CARD_FINALIZE_GUARD_V1"

HELPER_ANCHOR = """    async def _mark_card_failed(self, conversation_id: str) -> None:
"""

HELPER_REPLACEMENT = f'''    {MARKER}
    async def _jarvis_finalize_card_with_fallback(
        self,
        card: ActiveAICard,
        content: str,
        conversation_id: str,
        to_handle: str,
        send_meta: Optional[Dict[str, Any]],
        fallback_text: Optional[str] = None,
    ) -> bool:
        """Finalize one card with bounded retries and visible fallback."""
        final_text = (content or "").strip()
        if not final_text:
            final_text = "本次回复未能完整生成，请重新发送原问题。"
        if card.state == FINISHED:
            return True

        last_error: Optional[Exception] = None
        for attempt, delay in enumerate((0.0, 0.25, 0.75), start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                result = await asyncio.wait_for(
                    self._stream_ai_card(
                        card,
                        final_text,
                        finalize=True,
                    ),
                    timeout=8.0,
                )
                if result or card.state == FINISHED:
                    logger.info(
                        "dingtalk card finalize guard succeeded: "
                        "conversation_id=%s attempt=%s",
                        conversation_id,
                        attempt,
                    )
                    return True
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "dingtalk card finalize guard retry: "
                    "conversation_id=%s attempt=%s error=%s",
                    conversation_id,
                    attempt,
                    type(exc).__name__,
                )

        await self._mark_card_failed(conversation_id)
        delivery_text = (fallback_text or final_text).strip()
        delivered = False
        session_webhook = self._get_session_webhook(send_meta)
        if session_webhook and delivery_text:
            delivered = await self._send_via_session_webhook(
                session_webhook,
                delivery_text,
                bot_prefix="",
            )
        if not delivered and delivery_text:
            delivered = await self._try_open_api_fallback(
                delivery_text,
                to_handle,
                send_meta,
            )
        logger.error(
            "dingtalk card finalize guard exhausted: "
            "conversation_id=%s fallback_delivered=%s error=%s",
            conversation_id,
            delivered,
            type(last_error).__name__ if last_error else "none",
        )
        return delivered

    async def _mark_card_failed(self, conversation_id: str) -> None:
'''

ERROR_STATE_ANCHOR = '''    async def _on_consume_error(
        self,
        request: "AgentRequest",
        to_handle: str,
        err_text: str,
    ) -> None:
        """Send error emoji and error message via webhook."""
        meta = getattr(request, "channel_meta", None) or {}
        incoming_msg_id = str(meta.get("message_id") or "")
        conversation_id = str(meta.get("conversation_id") or "")
        if incoming_msg_id and conversation_id:
'''

ERROR_STATE_REPLACEMENT = '''    async def _on_consume_error(
        self,
        request: "AgentRequest",
        to_handle: str,
        err_text: str,
    ) -> None:
        """Send error emoji and error message via webhook."""
        meta = getattr(request, "channel_meta", None) or {}
        incoming_msg_id = str(meta.get("message_id") or "")
        conversation_id = str(meta.get("conversation_id") or "")
        error_delivered_via_card = False
        active_card = self._active_cards.get(conversation_id)
        if active_card and active_card.state not in (FINISHED, FAILED):
            setattr(request, "_precreated_card", None)
            error_delivered_via_card = (
                await self._jarvis_finalize_card_with_fallback(
                    active_card,
                    "本次回复未能完整生成，已结束等待状态。请重新发送原问题。",
                    conversation_id,
                    to_handle,
                    meta,
                )
            )
        if incoming_msg_id and conversation_id:
'''

ERROR_SEND_ANCHOR = '''        if session_webhook and full_err.strip():
            await self._send_via_session_webhook(
                session_webhook,
                full_err.strip(),
                bot_prefix="",
            )
'''

ERROR_SEND_REPLACEMENT = '''        if (
            not error_delivered_via_card
            and session_webhook
            and full_err.strip()
        ):
            await self._send_via_session_webhook(
                session_webhook,
                full_err.strip(),
                bot_prefix="",
            )
'''

COMPLETE_CARD_ANCHOR = '''                try:
                    await self._stream_ai_card(
                        card,
                        card_at + card_text,
                        finalize=True,
                    )
                except Exception:
                    logger.exception(
                        "dingtalk _on_process_completed: "
                        "card finalize failed",
                    )
                    await self._mark_card_failed(conversation_id)
                state.pop("nonstream_card", None)
'''

COMPLETE_CARD_REPLACEMENT = '''                await self._jarvis_finalize_card_with_fallback(
                    card,
                    card_at + card_text,
                    conversation_id,
                    to_handle,
                    send_meta,
                )
                state.pop("nonstream_card", None)
'''

UNUSED_CARD_ANCHOR = '''            try:
                await self._stream_ai_card(
                    unused_card,
                    self._build_ai_card_initial_text(),
                    finalize=True,
                )
            except Exception:
                logger.debug(
                    "dingtalk _on_process_completed: "
                    "unused card finalize failed",
                    exc_info=True,
                )
'''

UNUSED_CARD_REPLACEMENT = '''            await self._jarvis_finalize_card_with_fallback(
                unused_card,
                "本次未生成有效回复，已结束等待状态。请重新发送原问题。",
                conversation_id,
                to_handle,
                send_meta,
            )
'''

CYCLE_ANCHOR = '''    async def _on_process_completed(
        self,
        request: Any,
        to_handle: str,
        send_meta: Dict[str, Any],
    ) -> None:
'''

CYCLE_REPLACEMENT = '''    async def _finish_response_cycle(self, session_id: str) -> None:
        """Close cards left behind when a tracked turn is cancelled."""
        record = None
        inflight_turns = dict(getattr(self, "_inflight_turns", {}) or {})
        if not inflight_turns and hasattr(
            self,
            "_load_inflight_turns_from_disk",
        ):
            inflight_turns = self._load_inflight_turns_from_disk()
        for candidate in inflight_turns.values():
            if str(candidate.get("session_id") or "") == session_id:
                record = candidate
                break

        if record:
            conversation_id = str(record.get("conversation_id") or "")
            message_id = str(record.get("message_id") or "")
            card = self._active_cards.get(conversation_id)
            if card and card.state not in (FINISHED, FAILED):
                meta = {
                    "conversation_id": conversation_id,
                    "conversation_type": str(
                        record.get("conversation_type") or ""
                    ),
                    "sender_staff_id": str(
                        record.get("sender_staff_id") or ""
                    ),
                }
                await self._jarvis_finalize_card_with_fallback(
                    card,
                    "任务已停止，已结束等待状态。请重新发送原问题。",
                    conversation_id,
                    str(record.get("to_handle") or ""),
                    meta,
                )
            await self._complete_inflight_turn(message_id)

        await super()._finish_response_cycle(session_id)

    async def _on_process_completed(
        self,
        request: Any,
        to_handle: str,
        send_meta: Dict[str, Any],
    ) -> None:
'''


REPLACEMENTS = (
    (HELPER_ANCHOR, HELPER_REPLACEMENT),
    (ERROR_STATE_ANCHOR, ERROR_STATE_REPLACEMENT),
    (ERROR_SEND_ANCHOR, ERROR_SEND_REPLACEMENT),
    (CYCLE_ANCHOR, CYCLE_REPLACEMENT),
    (COMPLETE_CARD_ANCHOR, COMPLETE_CARD_REPLACEMENT),
    (UNUSED_CARD_ANCHOR, UNUSED_CARD_REPLACEMENT),
)


def resolve_channel_path() -> Path:
    spec = importlib.util.find_spec("qwenpaw.app.channels.dingtalk.channel")
    if spec is None or not spec.origin:
        raise SystemExit("QwenPaw DingTalk channel was not found")
    return Path(spec.origin)


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"Jarvis DingTalk card finalize guard already present: {path}")
        return
    if "JARVIS_DINGTALK_TURN_RECOVERY_PATCH_V1" not in source:
        raise SystemExit(
            "DingTalk turn recovery patch must be applied before card guard"
        )
    for anchor, _replacement in REPLACEMENTS:
        if source.count(anchor) != 1:
            raise SystemExit(
                "QwenPaw DingTalk card guard anchor did not match exactly "
                "once: " + anchor.splitlines()[0]
            )
    for anchor, replacement in REPLACEMENTS:
        source = source.replace(anchor, replacement)
    path.write_text(source, encoding="utf-8")
    print(f"Applied Jarvis DingTalk card finalize guard: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        type=Path,
        help="Channel path for tests; defaults to installed QwenPaw module",
    )
    args = parser.parse_args()
    patch(args.path or resolve_channel_path())


if __name__ == "__main__":
    main()
