#!/usr/bin/env python3
"""Guarantee that DingTalk AI Cards leave their processing state.

QwenPaw updates a non-streaming AI Card with completed message text and only
marks the card final after the whole response loop exits.  If the tracker
finishes before that later callback runs, the completed text and Thinking
reaction remain animated even though ``/stop`` correctly reports no task.
Final API failures and cancellations can produce the same stale state.

This patch keeps cards only for true streaming mode.  When DingTalk streaming
is disabled, inbound replies bypass the pre-created AI Card and use the
sessionWebhook/Open API final-message path, avoiding cards that the DingTalk
API can acknowledge without visibly leaving Processing.  Streaming cards still
use bounded finalize retries and fallbacks.  No prompt or model output is
persisted by this guard.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "# JARVIS_DINGTALK_CARD_FINALIZE_GUARD_V3"

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

PRECREATE_CARD_ANCHOR = '''        # Pre-create AI Card before LLM call so user sees it immediately.
        # The card is stored on request._precreated_card for streaming hooks
        # and on_event_message_completed to reuse.
        if self._ai_card_enabled() and conversation_id:
'''

PRECREATE_CARD_REPLACEMENT = '''        # Pre-create an AI Card only when true card streaming is enabled.
        # Final-only replies use the ordinary message path because DingTalk
        # can acknowledge a card finalize request while leaving it Processing.
        if (
            self._ai_card_enabled()
            and conversation_id
            and self.streaming_enabled
        ):
'''

NONSTREAM_CARD_ANCHOR = '''        # -- Card mode: accumulate text into pre-created card (no finalize) --
        if self._ai_card_enabled() and conversation_id and body.strip():
'''

NONSTREAM_CARD_REPLACEMENT = '''        # -- Card mode: only true streaming may use an inbound AI Card --
        if (
            self._ai_card_enabled()
            and conversation_id
            and body.strip()
            and self.streaming_enabled
        ):
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

COMPLETED_MESSAGE_ANCHOR = '''                # Stream update (not finalize) so user sees progress
                card_at = state.get("card_at_prefix") or ""
                try:
                    await self._stream_ai_card(
                        card,
                        card_at + merged,
                        finalize=False,
                    )
                except Exception:
                    logger.exception(
                        "dingtalk on_event_message_completed: "
                        "card stream failed, fallback to markdown",
                    )
                    await self._mark_card_failed(conversation_id)
                    state.pop("nonstream_card", None)
                    # Fall through to markdown mode below
                else:
                    # Deliver media parts separately (card only carries text)
                    await self._deliver_media_parts(
                        parts,
                        session_webhook,
                        to_handle,
                        send_meta,
                    )
                    return
'''

COMPLETED_MESSAGE_REPLACEMENT = '''                # Jarvis final-only mode emits one completed user-visible
                # message.  Finalize at that boundary instead of relying on a
                # later process callback which may no longer run.
                card_at = state.get("card_at_prefix") or ""
                try:
                    visible_complete = (
                        await self._jarvis_finalize_card_with_fallback(
                            card,
                            card_at + merged,
                            conversation_id,
                            to_handle,
                            send_meta,
                        )
                    )
                except Exception:
                    logger.exception(
                        "dingtalk on_event_message_completed: "
                        "terminal card update failed, fallback to markdown",
                    )
                    await self._mark_card_failed(conversation_id)
                    visible_complete = False

                state.pop("nonstream_card", None)
                if visible_complete:
                    state["jarvis_visible_complete"] = True
                    incoming_msg_id = str(
                        (send_meta or {}).get("message_id") or ""
                    )
                    if incoming_msg_id and conversation_id:
                        await self._send_emotion(
                            incoming_msg_id,
                            conversation_id,
                            "🤔Thinking",
                            recall=True,
                        )
                        await self._send_emotion(
                            incoming_msg_id,
                            conversation_id,
                            "🥳Done",
                        )
                    await self._complete_inflight_turn(incoming_msg_id)
                    # Deliver media parts separately (card only carries text)
                    await self._deliver_media_parts(
                        parts,
                        session_webhook,
                        to_handle,
                        send_meta,
                    )
                    return
                # Card and both fallbacks failed; let markdown delivery retry.
'''

DONE_REACTION_ANCHOR = '''        if incoming_msg_id and conversation_id:
            await self._send_emotion(
                incoming_msg_id,
                conversation_id,
                "🤔Thinking",
                recall=True,
            )
            await self._send_emotion(
                incoming_msg_id,
                conversation_id,
                "🥳Done",
            )
        await self._complete_inflight_turn(incoming_msg_id)
'''

DONE_REACTION_REPLACEMENT = '''        if (
            incoming_msg_id
            and conversation_id
            and not (state or {}).get("jarvis_visible_complete")
        ):
            await self._send_emotion(
                incoming_msg_id,
                conversation_id,
                "🤔Thinking",
                recall=True,
            )
            await self._send_emotion(
                incoming_msg_id,
                conversation_id,
                "🥳Done",
            )
        await self._complete_inflight_turn(incoming_msg_id)
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
    (PRECREATE_CARD_ANCHOR, PRECREATE_CARD_REPLACEMENT),
    (NONSTREAM_CARD_ANCHOR, NONSTREAM_CARD_REPLACEMENT),
    (ERROR_STATE_ANCHOR, ERROR_STATE_REPLACEMENT),
    (ERROR_SEND_ANCHOR, ERROR_SEND_REPLACEMENT),
    (CYCLE_ANCHOR, CYCLE_REPLACEMENT),
    (COMPLETE_CARD_ANCHOR, COMPLETE_CARD_REPLACEMENT),
    (UNUSED_CARD_ANCHOR, UNUSED_CARD_REPLACEMENT),
    (COMPLETED_MESSAGE_ANCHOR, COMPLETED_MESSAGE_REPLACEMENT),
    (DONE_REACTION_ANCHOR, DONE_REACTION_REPLACEMENT),
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
