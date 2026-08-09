#!/usr/bin/env python3
import os
import hashlib
import json
import asyncio
import logging
import re
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
import dingtalk_stream
from dingtalk_stream import AckMessage, CardReplier

from agent.prompt_compressor import compress_prompt
from memory.context import ContextManager
from core.executor_pool import ExecutorPool
from render.markdown import split_markdown
from render.card_data import build_card_data
from core.dingtalk_file_sender import DingTalkFileSender, find_pptx_paths
from core.recent_history import get_recent_items, get_recent_complete_turns, append_history
from core.memory_context import MemoryContext
from core.memory_candidate import MemoryCandidate
from core.memory_retriever import MemoryRetriever
from core.memory_writer import MemoryCandidateStore, MemoryWriter
from core.task_manager import COMPLETED, FAILED, RUNNING, WAITING_RESTART, TaskManager
from core.paths import PROJECT_ROOT, WORKSPACE_ROOT
from core.safe_download import download_to_path


BASE_DIR = PROJECT_ROOT
load_dotenv(BASE_DIR / ".env")

WORK_DIR = Path(os.getenv("CODEX_WORKDIR", str(WORKSPACE_ROOT)))
IMAGE_DIR = WORK_DIR / "uploads" / "dingtalk_images"
RAW_UPLOAD_DIR = WORK_DIR / "uploads" / "dingtalk_raw"
OUTPUT_DIR = WORK_DIR / "outputs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("jarvis")


APP_KEY = os.getenv("DINGTALK_APP_KEY") or os.getenv("DINGTALK_CLIENT_ID")
APP_SECRET = os.getenv("DINGTALK_APP_SECRET") or os.getenv("DINGTALK_CLIENT_SECRET")
CARD_TEMPLATE_ID = (os.getenv("DINGTALK_CARD_TEMPLATE_ID") or "").strip()
ALLOW_ALL_USERS = (os.getenv("DINGTALK_ALLOW_ALL_USERS") or "").strip().lower() in {"1", "true", "yes"}
ALLOWED_USER_IDS = {
    value.strip()
    for value in (os.getenv("DINGTALK_ALLOWED_USER_IDS") or "").split(",")
    if value.strip()
}
IMAGE_CONTEXT_MAX_AGE_SECONDS = 15 * 60
IMAGE_CONTEXT_MAX_FOLLOWUP_TURNS = 2


class CodexBotHandler(dingtalk_stream.ChatbotHandler):
    def __init__(self, dingtalk_client):
        super().__init__()
        self.dingtalk_client = dingtalk_client
        self.ctx = ContextManager(max_items=40)
        self.pool = ExecutorPool(max_concurrency=1)
        self.session_images = {}
        self.session_image_context = {}
        self.session_documents = {}
        self.file_sender = DingTalkFileSender(APP_KEY, APP_SECRET)
        self.task_manager = TaskManager()
        self.memory_retriever = MemoryRetriever()
        self.memory_context_loader = MemoryContext()
        self.memory_candidate_detector = MemoryCandidate()
        self.memory_candidate_store = MemoryCandidateStore()
        self.memory_writer = MemoryWriter()
        self.plugin_order = self._load_plugin_order()
        self.plugin_instances = self._init_plugin_instances()
        self.plugin_shadow_router = self._init_plugin_shadow_router()
        self.direct_image_send_router = self._init_direct_image_send_router()
        self.image_to_ppt_shadow_plugin = self._init_image_to_ppt_shadow_plugin()
        self.image_analyze_shadow_plugin = self._init_image_analyze_shadow_plugin()
        self.self_maintenance_shadow_plugin = self._init_self_maintenance_shadow_plugin()
        self.phase_a_shadow_router = self._init_phase_a_shadow_router()

    def _remember_session_images(self, session_id, image_paths):
        """Keep image follow-up context deliberately short-lived and turn-bounded."""
        paths = [str(path) for path in image_paths if path][-5:]
        self.session_images[session_id] = paths
        self.session_image_context[session_id] = {
            "paths": paths,
            "created_at": datetime.now().timestamp(),
            "remaining_text_turns": IMAGE_CONTEXT_MAX_FOLLOWUP_TURNS,
        }

    def _consume_recent_image_path(self, session_id):
        """Return a recent image for one eligible text turn, otherwise expire it."""
        context = self.session_image_context.get(session_id)
        if not context:
            return ""

        age_seconds = datetime.now().timestamp() - float(context.get("created_at") or 0)
        remaining_turns = int(context.get("remaining_text_turns") or 0)
        paths = [str(path) for path in context.get("paths") or [] if path]
        if age_seconds > IMAGE_CONTEXT_MAX_AGE_SECONDS or remaining_turns <= 0 or not paths:
            self.session_image_context.pop(session_id, None)
            return ""

        context["remaining_text_turns"] = remaining_turns - 1
        if context["remaining_text_turns"] <= 0:
            self.session_image_context.pop(session_id, None)
        return paths[-1]


    def _init_phase_a_shadow_router(self):
        try:
            from core.plugin_registry import create_shadow_router
            return create_shadow_router(self.plugin_instances)
        except Exception:
            logger.exception("intent_router_shadow_compare factory_failed")
            return None

    def _shadow_compare(self, *, legacy_route, legacy_action, legacy_reason, session_id, task_id, msgtype, text, raw=None, image_paths=None):
        """Fail-open observation; it does not invoke any plugin handle method."""
        try:
            from core.intent_router import canonical_action, classify_shadow_compare
            from core.plugin_base import MessageContext
            context = MessageContext.from_legacy(
                text=text or "", raw=raw or {}, session_id=session_id or "", task_id=task_id or "",
                msgtype=msgtype or "", image_paths=list(image_paths or self.session_images.get(session_id, [])),
            )
            shadow = self.phase_a_shadow_router.plan(context) if self.phase_a_shadow_router else None
            # Plugin routes are normalized from the one production canonical map;
            # non-plugin legacy branches keep their explicit execution action.
            if legacy_route in {"self_maintenance", "direct_file_send", "direct_image_send", "document_intake", "image_to_ppt", "image_analyze", "normal_chat"}:
                legacy_action = canonical_action(legacy_route)
            payload = classify_shadow_compare(
                legacy_route=legacy_route, legacy_action=legacy_action, legacy_reason=legacy_reason,
                shadow=shadow, context=context,
            )
        except Exception as exc:
            payload = {
                "session_id": session_id or "", "task_id": task_id or "", "msgtype": msgtype or "",
                "legacy_route": legacy_route, "legacy_action": legacy_action, "legacy_reason": legacy_reason,
                "shadow_route": "", "shadow_action": "", "shadow_reason": "", "matched": False,
                "mismatch_type": "shadow_error", "evaluated_plugins": [], "shadow_error": type(exc).__name__,
                "text_sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(), "text_length": len(text or ""),
            }
        logger.info("intent_router_shadow_compare %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _is_status_followup(text: str) -> bool:
        """Recognize short complaints about a missing reply without invoking Codex."""
        normalized = re.sub(r"\s+", "", text or "")
        return any(marker in normalized for marker in (
            "怎么又没反应", "怎么没反应", "怎么不回复", "为什么不回复",
            "还在吗", "有反应吗", "回复了吗", "进度怎么样",
        ))

    async def _reply_execution_status(self, session_id, incoming_message) -> None:
        task = self.task_manager.latest_active(session_id)
        if task:
            trigger = (task.get("payload") or {}).get("trigger_text") or "上一条请求"
            await self._reply_bot_message(
                incoming_message,
                f"已收到。上一条请求仍在执行：{trigger[:80]}。"
                "超过 240 秒会自动终止并反馈结果。",
            )
            return
        await self._reply_bot_message(
            incoming_message,
            "已收到。当前没有正在执行的任务；如需继续处理此前被中断的请求，请重新发送该请求。",
        )

    def _create_execution_task(self, session_id, user_text, raw=None, *, task_type="normal_chat") -> str:
        """Persist an accepted executable request before scheduling its coroutine."""
        if (user_text or "").strip().lower() in {"ping", "你好"}:
            return ""
        safe_raw = {
            key: value for key, value in dict(raw or {}).items()
            if key in {"robotCode", "conversationType", "openConversationId", "conversationId", "senderStaffId", "senderId"}
            and isinstance(value, (str, int, float, bool))
        }
        task_id = self.task_manager.create(
            task_type,
            session_id,
            {"raw": safe_raw, "trigger_text": (user_text or "")[:500]},
        )
        self.task_manager.record_event(task_id, "MESSAGE_ACCEPTED", "message accepted before coroutine scheduling")
        self.task_manager.transition(task_id, RUNNING, f"{task_type} queued")
        return task_id

    def _load_plugin_order(self):
        try:
            from core.plugin_registry import (
                compare_with_default_order,
                load_plugin_registry,
                validate_plugin_registry,
            )

            ok, msg, validated_order = validate_plugin_registry()
            order = load_plugin_registry()
            comparison = compare_with_default_order(order)
            if ok:
                logger.info("plugin_registry loaded order=%s", order)
            else:
                logger.warning(
                    "plugin_registry fallback_to_default order=%s reason=%s",
                    order,
                    msg,
                )
            if comparison.get("order_changed"):
                logger.warning("plugin_registry order_changed comparison=%s", comparison)
            return order or validated_order
        except Exception:
            logger.exception("plugin_registry load failed; fallback_to_default")
            return [
                "self_maintenance",
                "direct_file_send",
                "direct_image_send",
                "document_intake",
                "image_to_ppt",
                "image_analyze",
                "normal_chat",
            ]

    def _init_plugin_instances(self):
        try:
            from plugins.direct_file_send import DirectFileSendPlugin
            from plugins.direct_image_send import DirectImageSendPlugin
            from plugins.document_intake import DocumentIntakePlugin
            from plugins.image_analyze import ImageAnalyzePlugin
            from plugins.image_to_ppt import ImageToPptPlugin
            from plugins.normal_chat import NormalChatPlugin
            from plugins.self_maintenance import SelfMaintenancePlugin

            factories = {
                "self_maintenance": SelfMaintenancePlugin,
                "direct_file_send": DirectFileSendPlugin,
                "direct_image_send": DirectImageSendPlugin,
                "document_intake": DocumentIntakePlugin,
                "image_to_ppt": ImageToPptPlugin,
                "image_analyze": ImageAnalyzePlugin,
                "normal_chat": NormalChatPlugin,
            }
            instances = {}
            for index, name in enumerate(self.plugin_order):
                factory = factories.get(name)
                if not factory:
                    logger.warning("plugin_registry unknown plugin skipped name=%s", name)
                    continue
                plugin = factory()
                plugin.priority = (index + 1) * 10
                instances[name] = plugin
            return instances
        except Exception:
            logger.exception("plugin_registry plugin init failed")
            return {}

    def _plugin_enabled(self, name):
        return name in getattr(self, "plugin_order", [])

    def _plugin_instance(self, name):
        return getattr(self, "plugin_instances", {}).get(name)

    def _init_plugin_shadow_router(self):
        try:
            from core.intent_router import IntentRouter

            plugin = self._plugin_instance("direct_file_send")
            if not plugin:
                logger.warning("plugin_registry direct_file_send disabled or unavailable")
                return None
            return IntentRouter([plugin])
        except Exception:
            logger.exception("plugin_shadow init failed")
            return None

    def _init_direct_image_send_router(self):
        try:
            from core.intent_router import IntentRouter

            plugin = self._plugin_instance("direct_image_send")
            if not plugin:
                logger.warning("plugin_registry direct_image_send disabled or unavailable")
                return None
            return IntentRouter([plugin])
        except Exception:
            logger.exception("direct image plugin init failed")
            return None

    def _init_image_to_ppt_shadow_plugin(self):
        try:
            return self._plugin_instance("image_to_ppt")
        except Exception:
            logger.exception("plugin_shadow image_to_ppt init failed")
            return None

    def _init_image_analyze_shadow_plugin(self):
        try:
            return self._plugin_instance("image_analyze")
        except Exception:
            logger.exception("plugin_shadow image_analyze init failed")
            return None

    def _init_self_maintenance_shadow_plugin(self):
        try:
            return self._plugin_instance("self_maintenance")
        except Exception:
            logger.exception("plugin_shadow self_maintenance init failed")
            return None

    def _dispatch_image_to_ppt_plugin(self, text="", image_paths=None, metadata=None):
        if not self._plugin_enabled("image_to_ppt"):
            logger.info("plugin_shadow route=image_to_ppt handled=False reason=plugin_disabled")
            return None
        if self.image_to_ppt_shadow_plugin is None:
            logger.info("plugin_shadow route=image_to_ppt handled=False reason=plugin_unavailable")
            return None

        try:
            from core.plugin_base import PluginContext

            context = PluginContext(
                text=text or "",
                image_paths=list(image_paths or []),
                metadata=dict(metadata or {}),
            )
            matched = self.image_to_ppt_shadow_plugin.match(context)
            result = (
                self.image_to_ppt_shadow_plugin.handle(context)
                if matched
                else None
            )
            handled = bool(result.handled) if result is not None else False
            dispatch_metadata = result.metadata if result is not None else {}

            if (
                handled
                and dispatch_metadata.get("mode") == "image_to_ppt"
                and dispatch_metadata.get("image_paths")
            ):
                logger.info(
                    "plugin_dispatch route=image_to_ppt handled=True metadata=%s",
                    dispatch_metadata,
                )
                return result

            reason = "no_match"
            if handled and not dispatch_metadata.get("image_paths"):
                reason = "missing_image_paths"

            logger.info(
                "plugin_shadow route=image_to_ppt handled=False reason=%s metadata=%s",
                reason,
                dispatch_metadata,
            )
            return None
        except Exception:
            logger.exception("plugin_dispatch route=image_to_ppt failed")
            return None

    def _log_image_analyze_shadow(self, text="", image_paths=None, metadata=None):
        if not self._plugin_enabled("image_analyze"):
            logger.info("plugin_shadow route=image_analyze handled=False reason=plugin_disabled metadata={}")
            return
        if self.image_analyze_shadow_plugin is None:
            logger.info("plugin_shadow route=image_analyze handled=False reason=plugin_unavailable metadata={}")
            return

        try:
            from core.plugin_base import PluginContext

            context = PluginContext(
                text=text or "",
                image_paths=list(image_paths or []),
                metadata=dict(metadata or {}),
            )
            result = self.image_analyze_shadow_plugin.handle(context)
            shadow_metadata = result.metadata if result is not None else {}
            reason = ""
            if not result.handled:
                reason = shadow_metadata.get("reason") or "no_match"
                logger.info(
                    "plugin_shadow route=image_analyze handled=False reason=%s metadata=%s",
                    reason,
                    shadow_metadata,
                )
                return

            logger.info(
                "plugin_shadow route=image_analyze handled=True metadata=%s",
                shadow_metadata,
            )
        except Exception:
            logger.exception("plugin_shadow route=image_analyze failed")

    def _dispatch_image_analyze_plugin(self, text="", image_paths=None, metadata=None):
        if not self._plugin_enabled("image_analyze"):
            logger.info("plugin_shadow route=image_analyze handled=False reason=plugin_disabled metadata={}")
            return None
        if self.image_analyze_shadow_plugin is None:
            logger.info("plugin_shadow route=image_analyze handled=False reason=plugin_unavailable metadata={}")
            return None

        try:
            from core.plugin_base import PluginContext

            context = PluginContext(
                text=text or "",
                image_paths=list(image_paths or []),
                metadata=dict(metadata or {}),
            )
            result = self.image_analyze_shadow_plugin.handle(context)
            dispatch_metadata = result.metadata if result is not None else {}

            if (
                result
                and result.handled
                and dispatch_metadata.get("mode") == "image_analyze"
                and dispatch_metadata.get("image_paths")
            ):
                logger.info(
                    "plugin_dispatch route=image_analyze handled=True metadata=%s",
                    dispatch_metadata,
                )
                return result

            reason = dispatch_metadata.get("reason") or "no_match"
            logger.info(
                "plugin_shadow route=image_analyze handled=False reason=%s metadata=%s",
                reason,
                dispatch_metadata,
            )
            return None
        except Exception:
            logger.exception("plugin_dispatch route=image_analyze failed")
            return None

    def _dispatch_self_maintenance_plugin(self, text="", image_paths=None, files=None, metadata=None):
        if not self._plugin_enabled("self_maintenance"):
            logger.info("plugin_shadow route=self_maintenance handled=False reason=plugin_disabled metadata={}")
            return None
        if self.self_maintenance_shadow_plugin is None:
            logger.info("plugin_shadow route=self_maintenance handled=False reason=plugin_unavailable metadata={}")
            return None

        try:
            from core.plugin_base import PluginContext

            context = PluginContext(
                text=text or "",
                image_paths=list(image_paths or []),
                files=list(files or []),
                metadata=dict(metadata or {}),
            )
            result = self.self_maintenance_shadow_plugin.handle(context)
            dispatch_metadata = result.metadata if result is not None else {}

            if (
                result
                and result.handled
                and dispatch_metadata.get("mode") == "maintenance_proposal"
            ):
                logger.info(
                    "plugin_dispatch route=self_maintenance handled=True metadata=%s",
                    dispatch_metadata,
                )
                return result

            reason = dispatch_metadata.get("reason") or "no_match"
            logger.info(
                "plugin_shadow route=self_maintenance handled=False reason=%s metadata=%s",
                reason,
                dispatch_metadata,
            )
            return None
        except Exception:
            logger.exception("plugin_dispatch route=self_maintenance failed")
            return None

    def _dispatch_document_intake_plugin(self, raw=None, metadata=None):
        if not self._plugin_enabled("document_intake"):
            logger.info("plugin_dispatch route=document_intake handled=False reason=plugin_disabled metadata={}")
            return None

        plugin = self._plugin_instance("document_intake")
        if plugin is None:
            logger.info("plugin_dispatch route=document_intake handled=False reason=plugin_unavailable metadata={}")
            return None

        try:
            from core.plugin_base import PluginContext

            context_metadata = {
                "raw": raw or {},
                "raw_upload_dir": str(RAW_UPLOAD_DIR),
                "download_url_resolver": getattr(self, "get_image_download_url", None),
            }
            context_metadata.update(dict(metadata or {}))

            context = PluginContext(metadata=context_metadata)
            if not plugin.match(context):
                logger.info("plugin_dispatch route=document_intake handled=False reason=no_match metadata={}")
                return None

            result = plugin.handle(context)
            dispatch_metadata = result.metadata if result is not None else {}
            logger.info(
                "plugin_dispatch route=document_intake handled=%s metadata=%s",
                bool(result and result.handled),
                {
                    key: value
                    for key, value in dispatch_metadata.items()
                    if key not in {"file_text", "download_url_resolver"}
                },
            )
            return result
        except Exception:
            logger.exception("plugin_dispatch route=document_intake failed")
            return None

    def _dispatch_direct_file_send_plugin(self, text="", metadata=None):
        if self.plugin_shadow_router is None:
            logger.info("plugin_dispatch route=none handled=False reason=router_unavailable")
            return None

        if self._is_self_maintenance_text(text):
            logger.info("plugin_dispatch route=none handled=False reason=self_maintenance_text")
            return None

        try:
            from core.plugin_base import PluginContext

            context = PluginContext(
                text=text or "",
                metadata=dict(metadata or {}),
            )
            result = self.plugin_shadow_router.route(context)
            route = result.metadata.get("plugin") if result.metadata else None
            if not route and result.handled:
                route = "direct_file_send"
            route = route or "none"
            logger.info(
                "plugin_dispatch route=%s handled=%s files=%s",
                route,
                result.handled,
                result.files,
            )
            return result
        except Exception:
            logger.exception("plugin_dispatch route failed")
            return None

    def _dispatch_direct_image_send_plugin(self, text="", metadata=None):
        if self.direct_image_send_router is None:
            logger.info("plugin_dispatch route=none handled=False reason=image_router_unavailable")
            return None
        if self._is_self_maintenance_text(text):
            logger.info("plugin_dispatch route=none handled=False reason=self_maintenance_text")
            return None
        try:
            from core.plugin_base import PluginContext

            result = self.direct_image_send_router.route(
                PluginContext(text=text or "", metadata=dict(metadata or {}))
            )
            logger.info(
                "plugin_dispatch route=%s handled=%s files=%s",
                (result.metadata or {}).get("plugin", "none"), result.handled, result.files,
            )
            return result
        except Exception:
            logger.exception("direct image plugin dispatch failed")
            return None

    async def _reply_bot_message(self, incoming_message, text):
        delivered = await self._reply_card_safe(
            incoming_message,
            "Jarvis 助手",
            text
        )

        if not delivered:
            self._reply_markdown_safe(
                incoming_message,
                "Jarvis 助手",
                text
            )

    def _session_id(self, raw):
        conversation_id = raw.get("conversationId", "unknown_conversation")
        sender_id = raw.get("senderStaffId") or raw.get("senderId") or "unknown_sender"
        return f"{conversation_id}_{sender_id}"

    @staticmethod
    def _sender_id(raw):
        return str(raw.get("senderStaffId") or raw.get("senderId") or "").strip()

    def _sender_allowed(self, raw):
        sender_id = self._sender_id(raw)
        return bool(sender_id) and (ALLOW_ALL_USERS or sender_id in ALLOWED_USER_IDS)

    def _reply_markdown_safe(self, incoming_message, title, text):
        chunks = split_markdown(text)

        for idx, chunk in enumerate(chunks, 1):
            msg_title = title if len(chunks) == 1 else f"{title}（{idx}/{len(chunks)}）"
            try:
                self.reply_markdown(msg_title, chunk, incoming_message)
            except Exception:
                logger.exception("reply_markdown failed, fallback reply_text")
                try:
                    self.reply_text(chunk, incoming_message)
                except Exception:
                    logger.exception("reply_text failed")

    async def _reply_card_safe(self, incoming_message, question, answer):
        if not CARD_TEMPLATE_ID:
            logger.warning("DINGTALK_CARD_TEMPLATE_ID not set, fallback markdown")
            return False

        try:
            card_data = build_card_data(question, answer)

            replier = CardReplier(
                self.dingtalk_client,
                incoming_message
            )

            card_instance_id = await replier.async_create_and_deliver_card(
                CARD_TEMPLATE_ID,
                card_data,
                callback_type="STREAM",
                support_forward=True,
            )

            if card_instance_id:
                logger.info("card delivered, card_instance_id=%s", card_instance_id)
                return True

            logger.warning("card deliver returned empty card_instance_id")
            return False

        except Exception:
            logger.exception("reply card failed, fallback markdown")
            return False

    def _extract_text(self, incoming_message):
        text_obj = getattr(incoming_message, "text", None)
        content = getattr(text_obj, "content", None)
        if content:
            return content.strip()
        return ""

    def _load_content_obj(self, raw):
        content = raw.get("content")
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                return json.loads(content)
            except Exception:
                return {"raw": content}
        return {}

    def _find_values_by_key(self, obj, wanted_keys):
        result = []

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    lk = str(k).lower()
                    if lk in wanted_keys and v:
                        result.append(str(v))
                    walk(v)
            elif isinstance(x, list):
                for item in x:
                    walk(item)

        walk(obj)
        return result

    def _extract_image_codes(self, raw):
        content_obj = self._load_content_obj(raw)

        wanted_keys = {
            "downloadcode",
            "download_code",
            "imagedownloadcode",
            "picdownloadcode",
            "picturedownloadcode",
        }

        codes = self._find_values_by_key(content_obj, wanted_keys)

        # 有些钉钉消息会把 downloadCode 放在第一层
        for k in ["downloadCode", "download_code", "imageDownloadCode"]:
            if raw.get(k):
                codes.append(str(raw.get(k)))

        # 去重
        dedup = []
        for c in codes:
            if c and c not in dedup:
                dedup.append(c)

        return dedup

    def _extract_image_urls(self, raw):
        content_obj = self._load_content_obj(raw)

        urls = []

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if isinstance(v, str) and v.startswith("http") and any(ext in v.lower() for ext in [".png", ".jpg", ".jpeg", "media", "image"]):
                        urls.append(v)
                    walk(v)
            elif isinstance(x, list):
                for item in x:
                    walk(item)

        walk(content_obj)

        dedup = []
        for u in urls:
            if u and u not in dedup:
                dedup.append(u)

        return dedup

    def _is_document_message(self, raw):
        plugin = self._plugin_instance("document_intake")
        if not plugin:
            return False

        try:
            from core.plugin_base import PluginContext

            return plugin.match(PluginContext(metadata={"raw": raw or {}}))
        except Exception:
            logger.exception("plugin_dispatch route=document_intake match failed")
            return False

    def _safe_suffix(self, content_type, fallback=".png"):
        ctype = (content_type or "").lower()
        if "jpeg" in ctype or "jpg" in ctype:
            return ".jpg"
        if "png" in ctype:
            return ".png"
        if "webp" in ctype:
            return ".webp"
        if "json" in ctype:
            return ".json"
        if "markdown" in ctype:
            return ".md"
        if "text" in ctype:
            return ".txt"
        if "pdf" in ctype:
            return ".pdf"
        return fallback

    def _download_url_to_file(self, url, idx=1):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)

        stem = IMAGE_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{idx}"
        temporary_path = stem.with_suffix(".download")
        metadata = download_to_path(url, temporary_path)
        suffix = self._safe_suffix(str(metadata.get("content_type") or ""), ".png")
        path = stem.with_suffix(suffix)
        temporary_path.replace(path)
        return str(path)

    def _download_code_to_file(self, code, idx=1):
        if not hasattr(self, "get_image_download_url"):
            raise RuntimeError("当前 dingtalk_stream.ChatbotHandler 未发现 get_image_download_url 方法，无法用 downloadCode 下载图片。")

        url = self.get_image_download_url(code)
        if not url:
            raise RuntimeError("get_image_download_url 返回空，无法下载图片。")

        return self._download_url_to_file(url, idx)

    def _is_self_maintenance_text(self, text):
        text = text or ''
        if not text.strip():
            return False

        keywords = [
            '修改助手',
            '修复助手',
            '升级助手',
            '优化助手',
            '自维护',
            '自进化',
            '当前问题',
            '根因判断',
            '目标行为',
            '验证图文混发',
            '验证：',
            '验证:',
            '仍然正常',
            '这句话本身不要触发',
            '不要触发生成PPT',
            '不要触发生成ppt',
            '完成后只汇报',
            '不要改',
            '主链路',
            'bot.py',
            'prompt_compressor.py',
            'system_profile.md',
            'recent_history',
            'rolling_summary',
            'py_compile',
            'systemctl',
            'journalctl',
            'dingtalk_file_sender',
        ]

        return any(k in text for k in keywords)

    def _last_user_text(self, session_id):
        history = get_recent_items(session_id=session_id)
        if not history:
            history = self.ctx.get(session_id)

        for item in reversed(history or []):
            if item.get("role") == "user":
                text = (item.get("text") or "").strip()
                if text:
                    return text
        return ""

    def _build_image_analysis_prompt(self, user_text, image_paths):
        image_list = "\n".join([f"- {p}" for p in image_paths])
        return f"""
你是 Codex 工作入口。请根据随消息附带的图片回答用户问题。

用户问题：
{user_text or "（用户未提供文字说明，请先判断图片类型和可能的用户意图，再回答。）"}

图片路径：
{image_list}

要求：
1. 必须读取并分析附带图片，不要只根据文字猜测。

2. 先判断用户发图的意图，可能属于以下类别之一：
   - 【内容识别】：用户想了解图片里有什么（"这是什么"、"看看这张图"）
     → 简洁说明图片主要内容、场景和显著元素。
   - 【UI/界面评审】：用户想让你看界面渲染、排版、样式效果（"看看渲染"、"界面问题"、"排版不对"）
     → 聚焦界面布局、视觉呈现、样式问题，给出具体观察和改进建议。
   - 【数据解读】：用户想从图中提取数据信息（"这里的数据"、"统计图"）
     → 提取关键数据，总结趋势或要点。
   - 【故障排查】：用户截图反映某个问题或错误（"报错了"、"不对"、"怎么回事"）
     → 定位问题原因，给出排查方向和修复建议。
   - 【版式复刻/参考】：用户想参考这个图来做类似的东西（"照这个做"、"参考这个版式"）
     → 分析版式结构、配色、布局特点，为后续复刻提供结构化描述。
   - 【从示例学习】：用户想让 AI 学习某种模式或风格（"学习这个"、"记住这个风格"）
     → 提取模式特征、风格要点，总结为可复用的规范。

3. 根据判断的意图类型，给出对应深度的回答。不要对所有图片都只做内容识别。

4. 只有用户明确要求转 PPT 时，才提到 PPT 或执行转 PPT。

5. 回答要简洁、中文。
""".strip()

    def _build_image_to_ppt_prompt(self, user_text, image_paths):
        out_name = f"image_to_ppt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
        json_name = out_name.replace(".pptx", ".layout.json")

        out_path = OUTPUT_DIR / out_name
        json_path = OUTPUT_DIR / json_name

        image_list = "\n".join([f"- {p}" for p in image_paths])

        return f"""
你是 Codex 工作入口。现在要把用户给的图片重建成可编辑 PPT。

用户要求：
{user_text}

图片路径：
{image_list}

结构化 JSON 输出：
{json_path}

PPT 输出：
{out_path}

重要目标：
不要直接把整张截图铺到 PPT 里。必须先生成结构化 layout JSON，再用固定渲染器生成 PPT。

执行步骤：
1. 读取附带图片，识别页面标题、分层结构、卡片、文字、色块、连线、底部栏。
2. 生成 layout JSON，保存到：
   {json_path}
3. JSON 规范参考：
   {BASE_DIR}/docs/layout_ppt_schema.md
4. 调用固定渲染器：
   {BASE_DIR}/.venv/bin/python {BASE_DIR}/tools/render_layout_ppt.py {json_path} {out_path}
5. 检查 {out_path} 是否存在且大小大于 0。
6. 尽量做到文字、卡片、线条、分层区域可编辑。
7. 对复杂图标可以用简化符号或近似形状，不要为了图标完美而整页贴图。
8. 如果用户指定了品牌风格，严格按用户提供的品牌色和版式执行；否则使用简洁中性风格。
9. PPT 必须是 16:9。
10. 不要把完整代码贴回钉钉。

最终只返回：
- PPT 文件路径：{out_path}
- 是否生成成功
- 可编辑还原情况
- 哪些部分是近似还原

现在请直接执行。
""".strip()

    async def _handle_image_message(self, session_id, raw, incoming_message):
        try:
            IMAGE_DIR.mkdir(parents=True, exist_ok=True)

            # 保存 raw，方便后续排查
            debug_dir = WORK_DIR / "uploads" / "dingtalk_raw"
            debug_dir.mkdir(parents=True, exist_ok=True)
            raw_path = debug_dir / f"raw_picture_msg_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
            raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

            content_obj = self._load_content_obj(raw)

            named_codes = []
            text_parts = []

            def walk(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        lk = str(k).lower()

                        if isinstance(v, str):
                            if lk == "downloadcode" and v.strip():
                                named_codes.append(("downloadCode", v.strip()))
                            elif lk == "picturedownloadcode" and v.strip():
                                named_codes.append(("pictureDownloadCode", v.strip()))
                            elif lk in ["text", "title", "plain", "plaintext", "markdown"] and v.strip():
                                text_parts.append(v.strip())

                        walk(v)

                elif isinstance(obj, list):
                    for item in obj:
                        walk(item)

            walk(content_obj)

            # 兜底：兼容旧提取逻辑
            for c in self._extract_image_codes(raw):
                if c and ("unknown", c) not in named_codes:
                    named_codes.append(("unknown", c))

            # 去重，同时保持顺序
            seen = set()
            dedup_codes = []
            for name, code in named_codes:
                if code not in seen:
                    dedup_codes.append((name, code))
                    seen.add(code)

            # 关键：优先用 downloadCode；实测 pictureDownloadCode 会 500
            ordered_codes = (
                [(n, c) for n, c in dedup_codes if n == "downloadCode"]
                + [(n, c) for n, c in dedup_codes if n == "unknown"]
                + [(n, c) for n, c in dedup_codes if n == "pictureDownloadCode"]
            )

            user_text = "\n".join([x for x in text_parts if x]).strip()

            saved = []
            errors = []

            for idx, item in enumerate(ordered_codes, 1):
                name, code = item
                try:
                    saved_path = self._download_code_to_file(code, idx)
                    saved.append(saved_path)
                    logger.info("image downloaded by %s, path=%s", name, saved_path)
                    break
                except Exception as e:
                    logger.exception("download by %s failed", name)
                    errors.append(f"{name} 下载失败：{e}")

            if not saved:
                msg_lines = [
                    "图片消息已收到，但下载失败。",
                    "",
                    f"原始消息：{raw_path}",
                    "",
                    "错误摘要：",
                ]
                msg_lines.extend([f"- {e}" for e in errors[-5:]] or ["- 未拿到可用下载地址。"])
                msg = "\n".join(msg_lines)

                self._shadow_compare(legacy_route="unknown", legacy_action="unknown", legacy_reason="image_download_failed", session_id=session_id, task_id="", msgtype=raw.get("msgtype", ""), text=user_text, raw=raw)
                delivered = await self._reply_card_safe(
                    incoming_message,
                    "图片下载失败",
                    msg
                )
                if not delivered:
                    self._reply_markdown_safe(incoming_message, "图片下载失败", msg)
                return

            self._remember_session_images(session_id, saved)

            if user_text:
                self_maintenance_result = self._dispatch_self_maintenance_plugin(
                    text=user_text,
                    image_paths=saved,
                    metadata={
                        "session_id": session_id,
                        "msgtype": raw.get("msgtype", ""),
                        "source": "rich_text_image",
                    },
                )
                if self_maintenance_result:
                    self._shadow_compare(legacy_route="self_maintenance", legacy_action="codex_task", legacy_reason="plugin_matched", session_id=session_id, task_id="", msgtype=raw.get("msgtype", ""), text=user_text, raw=raw, image_paths=saved)
                    self_maintenance_metadata = self_maintenance_result.metadata
                    await self._handle_text_message(
                        session_id,
                        self_maintenance_metadata.get("trigger_text") or user_text,
                        incoming_message,
                        raw=raw,
                        maintenance_task=True,
                    )
                    return

            image_to_ppt_result = None
            if user_text:
                image_to_ppt_result = self._dispatch_image_to_ppt_plugin(
                    text=user_text,
                    image_paths=saved,
                    metadata={
                        "session_id": session_id,
                        "msgtype": raw.get("msgtype", ""),
                        "source": "rich_text_image",
                    },
                )
            if image_to_ppt_result:
                self._shadow_compare(legacy_route="image_to_ppt", legacy_action="codex_chat", legacy_reason="plugin_matched", session_id=session_id, task_id="", msgtype=raw.get("msgtype", ""), text=user_text, raw=raw, image_paths=saved)
                image_to_ppt_metadata = image_to_ppt_result.metadata
                plugin_image_paths = image_to_ppt_metadata.get("image_paths") or saved
                plugin_user_text = image_to_ppt_metadata.get("trigger_text") or user_text
                logger.info("richText image+text detected by plugin, continue image_to_ppt, text_length=%s", len(plugin_user_text or ""))
                await self._handle_text_message(
                    session_id,
                    plugin_user_text,
                    incoming_message,
                    raw=raw,
                    image_paths=plugin_image_paths,
                    image_to_ppt=True,
                )
                return

            analysis_text = user_text or self._last_user_text(session_id)
            image_analyze_metadata = {
                "session_id": session_id,
                "msgtype": raw.get("msgtype", ""),
                "source": "image_analysis",
            }
            image_analyze_result = self._dispatch_image_analyze_plugin(
                text=analysis_text,
                image_paths=saved,
                metadata=image_analyze_metadata,
            )
            if not image_analyze_result and not user_text:
                analysis_text = "请分析这张图片，先判断图片类型和可能的用户意图，再给出对应深度的回答。"
                image_analyze_result = self._dispatch_image_analyze_plugin(
                    text=analysis_text,
                    image_paths=saved,
                    metadata=image_analyze_metadata,
                )
            if image_analyze_result:
                self._shadow_compare(legacy_route="image_analyze", legacy_action="codex_chat", legacy_reason="plugin_matched", session_id=session_id, task_id="", msgtype=raw.get("msgtype", ""), text=analysis_text, raw=raw, image_paths=saved)
                image_analyze_metadata = image_analyze_result.metadata
                analysis_text = image_analyze_metadata.get("trigger_text") or analysis_text
                saved = image_analyze_metadata.get("image_paths") or saved
            elif not analysis_text:
                analysis_text = "请分析这张图片，先判断图片类型和可能的用户意图，再给出对应深度的回答。"
            if not image_analyze_result:
                self._shadow_compare(legacy_route="image_analyze", legacy_action="codex_chat", legacy_reason="legacy_forced_analysis", session_id=session_id, task_id="", msgtype=raw.get("msgtype", ""), text=analysis_text, raw=raw, image_paths=saved)

            logger.info("image message routed to image analysis, text=%s", analysis_text)
            await self._handle_text_message(
                session_id,
                analysis_text,
                incoming_message,
                raw=raw,
                image_paths=saved,
                image_analysis=True,
            )

        except Exception as e:
            logger.exception("handle image message failed")
            self._reply_markdown_safe(
                incoming_message,
                "图片处理异常",
                f"图片处理失败：{e}"
            )

    async def _handle_direct_ppt_message(self, session_id, user_text, incoming_message=None, raw=None):
        try:
            # 直接发送已有 PPT 文件时，send_file_for_raw 只需要纯字符串会话字段。
            # 不能把 incoming_message / ChatbotMessage 这类 SDK 对象塞进 raw，否则 requests json 序列化会失败。
            send_raw = {}

            # 保留原 raw 中的简单字段，丢弃对象、dict、list 等复杂类型
            for k, v in dict(raw or {}).items():
                if isinstance(v, (str, int, float, bool)) or v is None:
                    send_raw[k] = v

            sid = str(session_id or "").strip()

            # 对当前单聊/会话，session_id 就是日志里的 cid...，之前原生文件发送成功也是依赖这个会话 id。
            if sid:
                send_raw["conversationId"] = sid
                send_raw["openConversationId"] = sid
                send_raw["open_conversation_id"] = sid

            # 从 incoming_message 里只取字符串属性，绝不取对象
            if incoming_message is not None:
                safe_attrs = {
                    "senderStaffId": ["sender_staff_id", "senderStaffId"],
                    "sender_staff_id": ["sender_staff_id", "senderStaffId"],
                    "senderId": ["sender_id", "senderId"],
                    "robotCode": ["robot_code", "robotCode"],
                    "conversationType": ["conversation_type", "conversationType"],
                }

                for key, attrs in safe_attrs.items():
                    for attr in attrs:
                        value = getattr(incoming_message, attr, None)
                        if isinstance(value, (str, int, float)) and str(value).strip():
                            send_raw[key] = str(value).strip()
                            break

            logger.info("direct ppt send raw keys=%s conversationId=%s openConversationId=%s",
                        sorted(send_raw.keys()),
                        send_raw.get("conversationId"),
                        send_raw.get("openConversationId"))

            native_results = await asyncio.to_thread(
                self._send_native_files_if_any,
                send_raw,
                user_text or ""
            )

            if native_results:
                ok_items = [x for x in native_results if x.get("ok")]
                fail_items = [x for x in native_results if not x.get("ok")]

                if ok_items:
                    msg = "PPT 已通过钉钉原生文件气泡发送。"
                else:
                    errs = "; ".join([str(x.get("error", "未知错误")) for x in fail_items[:3]])
                    msg = f"PPT 文件发送失败：{errs}"
            else:
                msg = "没有识别到可发送的 .pptx 文件路径。"

            if incoming_message is None:
                logger.warning("direct ppt handled but incoming_message is None, msg=%s", msg)
                return

            delivered = await self._reply_card_safe(
                incoming_message,
                "Jarvis 助手",
                msg
            )

            if not delivered:
                self._reply_markdown_safe(
                    incoming_message,
                    "Jarvis 助手",
                    msg
                )

        except Exception as e:
            logger.exception("handle direct ppt message failed")
            if incoming_message is not None:
                self._reply_markdown_safe(
                    incoming_message,
                    "Jarvis 助手异常",
                    f"PPT 文件发送失败：{e}"
                )

    def _build_send_raw(self, session_id, incoming_message, raw):
        """Keep only transport-safe conversation fields for native media APIs."""
        send_raw = {
            key: value
            for key, value in dict(raw or {}).items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if str(session_id or "").strip():
            send_raw.setdefault("conversationId", str(session_id).strip())
            send_raw.setdefault("openConversationId", str(session_id).strip())
        if incoming_message is not None:
            for key, attrs in {
                "senderStaffId": ["sender_staff_id", "senderStaffId"],
                "senderId": ["sender_id", "senderId"],
                "robotCode": ["robot_code", "robotCode"],
                "conversationType": ["conversation_type", "conversationType"],
            }.items():
                for attr in attrs:
                    value = getattr(incoming_message, attr, None)
                    if isinstance(value, (str, int, float)) and str(value).strip():
                        send_raw[key] = str(value).strip()
                        break
        return send_raw

    async def _handle_direct_image_message(self, session_id, image_paths, incoming_message=None, raw=None):
        try:
            send_raw = self._build_send_raw(session_id, incoming_message, raw)
            native_results = await asyncio.to_thread(
                self._send_native_images, send_raw, image_paths
            )
            ok_items = [item for item in native_results if item.get("ok")]
            fail_items = [item for item in native_results if not item.get("ok")]
            if ok_items and not fail_items:
                msg = "图片已通过钉钉原生图片消息发送。"
            elif ok_items:
                msg = "部分图片已通过钉钉原生图片消息发送；其余发送失败。"
            else:
                errors = "; ".join(str(item.get("error", "未知错误")) for item in fail_items[:3])
                msg = f"图片发送失败：{errors or '未收到钉钉成功响应'}"
            if incoming_message is not None:
                await self._reply_bot_message(incoming_message, msg)
        except Exception:
            logger.exception("handle direct image message failed")
            if incoming_message is not None:
                await self._reply_bot_message(incoming_message, "图片发送失败，本次未确认送达。")


    async def _handle_document_message(self, session_id, raw, incoming_message):
        try:
            self._shadow_compare(legacy_route="document_intake", legacy_action="document_task", legacy_reason="document_message", session_id=session_id, task_id="", msgtype=raw.get("msgtype", ""), text="", raw=raw)
            result = self._dispatch_document_intake_plugin(
                raw=raw,
                metadata={
                    "session_id": session_id,
                    "msgtype": raw.get("msgtype", ""),
                    "source": "document_message",
                },
            )
            if not result or not result.handled:
                await self._reply_bot_message(
                    incoming_message,
                    "文件消息已收到，但 document_intake 插件未能处理。",
                )
                return

            metadata = result.metadata or {}
            document_kind = metadata.get("document_kind")
            saved_path = metadata.get("saved_path", "")
            filename = metadata.get("filename", "dingtalk_file")

            if document_kind == "download_failed":
                msg_lines = [
                    "文件消息已收到，但下载失败。",
                    "",
                    f"文件名：{filename}",
                    f"原始消息：{metadata.get('raw_path', '')}",
                    "",
                    "错误摘要：",
                ]
                msg_lines.extend([f"- {e}" for e in metadata.get("errors", [])])
                await self._reply_bot_message(incoming_message, "\n".join(msg_lines))
                return

            if document_kind == "text_decode_failed":
                await self._reply_bot_message(incoming_message, result.text)
                return

            if document_kind == "docx":
                self.session_documents[session_id] = {
                    "filename": filename,
                    "file_text": metadata.get("file_text") or "",
                    "sidecar_path": metadata.get("sidecar_path") or "",
                }
                await self._reply_bot_message(incoming_message, result.text)
                return

            if document_kind in {"legacy_doc", "docx_parse_failed"}:
                await self._reply_bot_message(incoming_message, result.text)
                return

            if document_kind == "text":
                file_text = metadata.get("file_text") or result.text or ""
                self_maintenance_result = self._dispatch_self_maintenance_plugin(
                    text=file_text,
                    files=[saved_path],
                    metadata={
                        "session_id": session_id,
                        "msgtype": raw.get("msgtype", ""),
                        "source": "document_intake_text",
                        "filename": filename,
                        "path": saved_path,
                    },
                )
                if self_maintenance_result:
                    self_maintenance_metadata = self_maintenance_result.metadata
                    await self._handle_text_message(
                        session_id,
                        self_maintenance_metadata.get("trigger_text") or file_text,
                        incoming_message,
                        raw=raw,
                        maintenance_task=True,
                    )
                    return

                await self._handle_text_message(
                    session_id,
                    file_text,
                    incoming_message,
                    raw=raw,
                )
                return

            if document_kind == "image":
                saved = [saved_path]
                self._remember_session_images(session_id, saved)
                analysis_text = "请分析这张图片，先判断图片类型和可能的用户意图，再给出对应深度的回答。"
                image_analyze_result = self._dispatch_image_analyze_plugin(
                    text=analysis_text,
                    image_paths=saved,
                    metadata={
                        "session_id": session_id,
                        "msgtype": raw.get("msgtype", ""),
                        "source": "document_intake_image",
                        "filename": filename,
                    },
                )
                if image_analyze_result:
                    metadata = image_analyze_result.metadata
                    analysis_text = metadata.get("trigger_text") or analysis_text
                    saved = metadata.get("image_paths") or saved

                await self._handle_text_message(
                    session_id,
                    analysis_text,
                    incoming_message,
                    raw=raw,
                    image_paths=saved,
                    image_analysis=True,
                )
                return

            if document_kind in {"save_only", "unknown"}:
                await self._reply_bot_message(incoming_message, result.text)
                return

            await self._reply_bot_message(incoming_message, result.text or "文件已收到，当前暂未解析该类型。")

        except Exception as e:
            logger.exception("handle document message failed")
            self._reply_markdown_safe(
                incoming_message,
                "文件处理异常",
                f"文件处理失败：{e}",
            )


    async def process(self, callback: dingtalk_stream.CallbackMessage):
        try:
            raw = callback.data or {}
            incoming_message = dingtalk_stream.ChatbotMessage.from_dict(raw)
            session_id = self._session_id(raw)
            msgtype = raw.get("msgtype", "")

            if not self._sender_allowed(raw):
                logger.warning("unauthorized dingtalk sender rejected sender_id=%s", self._sender_id(raw) or "-")
                self._reply_markdown_safe(incoming_message, "Jarvis", "当前账号未被授权使用此助手。")
                return AckMessage.STATUS_OK, "OK"

            if self._is_document_message(raw):
                logger.info("document message session=%s msgtype=%s", session_id, msgtype)
                asyncio.create_task(
                    self._handle_document_message(session_id, raw, incoming_message)
                )
                return AckMessage.STATUS_OK, "OK"

            user_text = self._extract_text(incoming_message)

            if user_text:
                logger.info("message session=%s text_length=%s", session_id, len(user_text))
                if self._is_status_followup(user_text):
                    self._shadow_compare(legacy_route="state_followup", legacy_action="reply_status", legacy_reason="status_followup", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    await self._reply_execution_status(session_id, incoming_message)
                    return AckMessage.STATUS_OK, "OK"
                self_maintenance_result = self._dispatch_self_maintenance_plugin(
                    text=user_text,
                    metadata={
                        "session_id": session_id,
                        "msgtype": msgtype,
                        "source": "text_message",
                    },
                )
                if self_maintenance_result:
                    self._shadow_compare(legacy_route="self_maintenance", legacy_action="codex_task", legacy_reason="plugin_matched", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    self_maintenance_metadata = self_maintenance_result.metadata
                    asyncio.create_task(
                        self._handle_text_message(
                            session_id,
                            self_maintenance_metadata.get("trigger_text") or user_text,
                            incoming_message,
                            raw=raw,
                            maintenance_task=True,
                            task_id=self._create_execution_task(session_id, self_maintenance_metadata.get("trigger_text") or user_text, raw, task_type="self_maintenance"),
                        )
                    )
                    return AckMessage.STATUS_OK, "OK"

                pending_candidate = self.memory_candidate_store.pending(session_id)
                if self.memory_candidate_detector.is_cancellation(user_text) and pending_candidate:
                    self.memory_candidate_store.update_status(
                        pending_candidate["candidate_id"],
                        "CANCELLED",
                        cancelled_time=datetime.now().astimezone().isoformat(timespec="seconds"),
                        cancel_reason="用户取消保存",
                    )
                    self._shadow_compare(legacy_route="memory_guard", legacy_action="memory_write_or_reply", legacy_reason="memory_cancel", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    await self._reply_bot_message(incoming_message, "已取消保存这条长期记忆。")
                    return AckMessage.STATUS_OK, "OK"

                if self.memory_candidate_detector.is_confirmation(user_text) and pending_candidate:
                    write_result = self.memory_writer.write(pending_candidate)
                    self.memory_candidate_store.update_status(
                        pending_candidate["candidate_id"],
                        "SAVED",
                        saved_time=datetime.now().astimezone().isoformat(timespec="seconds"),
                        write_operation=write_result.get("operation"),
                        backup_dir=write_result.get("backup_dir"),
                    )
                    self._shadow_compare(legacy_route="memory_guard", legacy_action="memory_write_or_reply", legacy_reason="memory_confirm", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    await self._reply_bot_message(
                        incoming_message,
                        f"已保存长期记忆到：{pending_candidate['target_file']}\n"
                        f"处理方式：{write_result.get('operation')}\n"
                        f"备份目录：{write_result.get('backup_dir')}",
                    )
                    return AckMessage.STATUS_OK, "OK"

                memory_analysis = self.memory_candidate_detector.analyze(user_text)
                if memory_analysis.get("action") == "intake":
                    self._shadow_compare(legacy_route="memory_guard", legacy_action="memory_write_or_reply", legacy_reason="memory_intake", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    intake = self.memory_candidate_store.create_intake(session_id, user_text)
                    await self._reply_bot_message(
                        incoming_message,
                        "已识别为记忆摄取请求，本条指令不会写入长期记忆。\n"
                        f"摄取会话有效期至：{intake.get('expires_at')}\n"
                        "请继续上传 TXT 或 DOCX；文件内容仍需生成候选并由你确认后才会保存。",
                    )
                    return AckMessage.STATUS_OK, "OK"

                if memory_analysis.get("action") == "duplicate":
                    self._shadow_compare(legacy_route="memory_guard", legacy_action="memory_write_or_reply", legacy_reason="memory_duplicate", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    candidate = memory_analysis.get("candidate") or {}
                    await self._reply_bot_message(
                        incoming_message,
                        "已有语义相同的长期记忆，无需重复保存。\n"
                        f"分类：{candidate.get('category', '长期记忆')}\n"
                        f"现有位置：{candidate.get('existing_memory_ref') or candidate.get('target_file')}",
                    )
                    return AckMessage.STATUS_OK, "OK"

                candidate = memory_analysis.get("candidate") if memory_analysis.get("action") == "candidate" else None
                if candidate:
                    record = self.memory_candidate_store.create(candidate, session_id)
                    if not record.get("_created"):
                        self._shadow_compare(legacy_route="memory_guard", legacy_action="memory_write_or_reply", legacy_reason="memory_pending_duplicate", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                        if record.get("_duplicate_scope") == "pending":
                            message = "已有相同候选正在等待确认，无需重复生成。请回复“保存”或“取消”。"
                        else:
                            message = "已有语义相同的长期记忆，无需重复保存。"
                        await self._reply_bot_message(incoming_message, message)
                        return AckMessage.STATUS_OK, "OK"

                    self._shadow_compare(legacy_route="memory_guard", legacy_action="memory_write_or_reply", legacy_reason="memory_candidate", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    operation_labels = {
                        "create": "新增",
                        "merge": "合并更新",
                        "replace": "替换旧记忆",
                    }
                    operation = record.get("operation") or "create"
                    await self._reply_bot_message(
                        incoming_message,
                        "检测到一条长期记忆候选：\n\n"
                        f"分类：{record['category']}\n"
                        f"记忆类型：{record.get('memory_type')}\n"
                        f"保存位置：{record['target_file']}\n"
                        f"处理方式：{operation_labels.get(operation, operation)}\n\n"
                        f"归一化内容：\n{record['normalized_content']}\n\n"
                        "请回复“保存”或“取消”。",
                    )
                    return AckMessage.STATUS_OK, "OK"

                document_context = self.session_documents.get(session_id)
                if document_context and self._plugin_instance("document_intake").should_use_word_context(user_text):
                    user_text = (
                        f"{user_text}\n\n"
                        f"以下是刚上传的 Word 文件《{document_context['filename']}》解析文本：\n"
                        f"{document_context['file_text']}"
                    )

                plugin_result = self._dispatch_direct_file_send_plugin(
                    text=user_text,
                    metadata={
                        "session_id": session_id,
                        "msgtype": msgtype,
                    },
                )

                if plugin_result and plugin_result.handled:
                    self._shadow_compare(legacy_route="direct_file_send", legacy_action="send_file", legacy_reason="plugin_matched", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    if plugin_result.files:
                        asyncio.create_task(
                            self._handle_direct_ppt_message(
                                session_id,
                                "\n".join(plugin_result.files),
                                incoming_message,
                                raw=raw,
                            )
                        )
                    else:
                        asyncio.create_task(
                            self._reply_bot_message(
                                incoming_message,
                                plugin_result.text or "识别到 PPT 文件路径，但没有可发送的文件。"
                            )
                        )
                    return AckMessage.STATUS_OK, "OK"

                image_plugin_result = self._dispatch_direct_image_send_plugin(
                    text=user_text,
                    metadata={
                        "session_id": session_id,
                        "msgtype": msgtype,
                        "recent_history": get_recent_items(session_id=session_id),
                    },
                )
                if image_plugin_result and image_plugin_result.handled:
                    self._shadow_compare(legacy_route="direct_image_send", legacy_action="send_image", legacy_reason="plugin_matched", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    asyncio.create_task(
                        self._handle_direct_image_message(
                            session_id, image_plugin_result.files, incoming_message, raw=raw,
                        )
                    )
                    return AckMessage.STATUS_OK, "OK"

                recent_image_path = self._consume_recent_image_path(session_id)
                image_to_ppt_result = self._dispatch_image_to_ppt_plugin(
                    text=user_text,
                    metadata={
                        "session_id": session_id,
                        "msgtype": msgtype,
                        "source": "text_message",
                        "recent_image_path": recent_image_path,
                    },
                )

                if image_to_ppt_result:
                    self._shadow_compare(legacy_route="image_to_ppt", legacy_action="codex_chat", legacy_reason="plugin_matched", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                    image_to_ppt_metadata = image_to_ppt_result.metadata
                    asyncio.create_task(
                        self._handle_text_message(
                            session_id,
                            image_to_ppt_metadata.get("trigger_text") or user_text,
                            incoming_message,
                            raw=raw,
                            image_paths=image_to_ppt_metadata.get("image_paths") or self.session_images.get(session_id, []),
                            image_to_ppt=True,
                            task_id=self._create_execution_task(session_id, image_to_ppt_metadata.get("trigger_text") or user_text, raw, task_type="image_to_ppt"),
                        )
                    )
                else:
                    image_analyze_result = None
                    if recent_image_path:
                        image_analyze_result = self._dispatch_image_analyze_plugin(
                            text=user_text,
                            metadata={
                                "session_id": session_id,
                                "msgtype": msgtype,
                                "source": "text_message",
                                "recent_image_path": recent_image_path,
                            },
                        )
                    if image_analyze_result:
                        self._shadow_compare(legacy_route="image_analyze", legacy_action="codex_chat", legacy_reason="plugin_matched", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                        image_analyze_metadata = image_analyze_result.metadata
                        asyncio.create_task(
                        self._handle_text_message(
                            session_id,
                            image_analyze_metadata.get("trigger_text") or user_text,
                            incoming_message,
                            raw=raw,
                            image_paths=image_analyze_metadata.get("image_paths") or self.session_images.get(session_id, []),
                            image_analysis=True,
                            task_id=self._create_execution_task(session_id, image_analyze_metadata.get("trigger_text") or user_text, raw, task_type="image_analysis"),
                            )
                        )
                    else:
                        self._shadow_compare(legacy_route="normal_chat", legacy_action="codex_chat", legacy_reason="legacy_fallback", session_id=session_id, task_id="", msgtype=msgtype, text=user_text, raw=raw)
                        asyncio.create_task(
                        self._handle_text_message(
                            session_id,
                            user_text,
                            incoming_message,
                            raw=raw,
                            task_id=self._create_execution_task(session_id, user_text, raw),
                        )
                        )

                return AckMessage.STATUS_OK, "OK"

            # 非文本消息：只有图片类消息进入图片流程，避免 file/downloadCode 被误当图片。
            codes = self._extract_image_codes(raw)
            urls = self._extract_image_urls(raw)

            if msgtype in ["picture", "image"] or (msgtype != "file" and (codes or urls)):
                logger.info("image message session=%s msgtype=%s codes=%s urls=%s", session_id, msgtype, len(codes), len(urls))
                asyncio.create_task(
                    self._handle_image_message(session_id, raw, incoming_message)
                )
                return AckMessage.STATUS_OK, "OK"

            msgtype = raw.get("msgtype", "")

            from pathlib import Path as _Path
            from datetime import datetime as _datetime
            import json as _json

            work_dir = _Path(os.getenv("CODEX_WORKDIR", str(WORKSPACE_ROOT)))
            debug_dir = work_dir / "uploads" / "dingtalk_raw"
            debug_dir.mkdir(parents=True, exist_ok=True)

            prefix = "raw_picture_msg" if msgtype in ["picture", "image"] else "raw_non_text_msg"
            raw_path = debug_dir / f"{prefix}_{_datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
            raw_path.write_text(_json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

            logger.info("non-text message saved, msgtype=%s raw_path=%s raw keys=%s", msgtype, raw_path, list(raw.keys()))

            if msgtype in ["picture", "image"]:
                asyncio.create_task(
                    self._reply_card_safe(
                        incoming_message,
                        "图片消息已保存",
                        f"图片原始消息已保存：\n{raw_path}\n\n下一步我会根据这个 raw 结构适配图片下载。"
                    )
                )

            self._shadow_compare(legacy_route="unknown", legacy_action="unknown", legacy_reason="unrecognized_message", session_id=session_id, task_id="", msgtype=msgtype, text="", raw=raw)
            return AckMessage.STATUS_OK, "OK"

        except Exception:
            logger.exception("process failed")
            return AckMessage.STATUS_OK, "OK"

    async def _handle_text_message(self, session_id, user_text, incoming_message, raw=None, image_paths=None, image_to_ppt=False, image_analysis=False, maintenance_task=False, task_id=""):
        try:
            # Direct callers (image/document handling) retain durable tracking.
            if not task_id:
                task_type = "self_maintenance" if maintenance_task else (
                    "image_to_ppt" if image_to_ppt else "image_analysis" if image_analysis else "normal_chat"
                )
                task_id = self._create_execution_task(session_id, user_text, raw, task_type=task_type)
            # 先读取落盘历史，再追加当前用户消息。
            # 这样“最近上下文”不重复包含当前问题，当前问题由 compress_prompt 的【用户当前任务】承载。
            history_context = get_recent_complete_turns(session_id=session_id)

            # 兼容当前进程内的上下文：如果落盘历史为空，就退回 self.ctx。
            if not history_context:
                history_context = self.ctx.get(session_id)

            self.ctx.add(session_id, "user", user_text)
            append_history(session_id=session_id, role="user", content=user_text)

            memory_files = self.memory_retriever.retrieve(user_text)
            memory_context = self.memory_context_loader.load(memory_files)
            logger.info(
                "memory_retrieval files=%s bytes=%s",
                [str(path) for path in memory_files],
                len(memory_context.encode("utf-8")),
            )

            if image_to_ppt:
                prompt = self._build_image_to_ppt_prompt(user_text, image_paths or [])
                answer = await self.pool.run(
                    prompt,
                    image_paths=image_paths or [],
                    task_manager=self.task_manager,
                    task_id=task_id,
                    allow_project_access=True,
                )
            elif image_analysis:
                prompt = self._build_image_analysis_prompt(user_text, image_paths or [])
                answer = await self.pool.run(prompt, image_paths=image_paths or [], task_manager=self.task_manager, task_id=task_id)
            else:
                context = history_context
                if maintenance_task:
                    user_text = (
                        "以下请求只允许诊断并输出修改建议，不得修改 Jarvis 源码、配置或依赖，"
                        "不得调用 sudo、systemctl、重启或回滚服务。\n\n" + user_text
                    )
                prompt = compress_prompt(user_text, context, memory_context=memory_context)
                answer = await self.pool.run(prompt, task_manager=self.task_manager, task_id=task_id)

            if task_id:
                if self.pool.is_failure_response(answer):
                    self.task_manager.transition(task_id, FAILED, "execution failed", error_text=answer)

            self.ctx.add(session_id, "assistant", answer)
            append_history(session_id=session_id, role="assistant", content=answer)

            native_note = ""
            pptx_paths = find_pptx_paths(answer)
            if pptx_paths:
                logger.info("pptx path detected, try native file send, paths=%s", pptx_paths)
                try:
                    native_results = await asyncio.to_thread(
                        self._send_native_files_if_any,
                        raw or {},
                        answer
                    )

                    logger.info("native file send results=%s", native_results)

                    if native_results:
                        ok_items = [x for x in native_results if x.get("ok")]
                        fail_items = [x for x in native_results if not x.get("ok")]

                        if ok_items:
                            native_note += "\n\n已通过钉钉原生文件气泡发送 PPT。"

                        if fail_items:
                            errs = "; ".join([x.get("error", "未知错误") for x in fail_items[:2]])
                            native_note += f"\n\n原生文件发送失败：{errs}"
                    else:
                        native_note += "\n\n未识别到可发送的 .pptx 文件路径。"

                except Exception as e:
                    logger.exception("native file send crashed")
                    native_note += f"\n\n原生文件发送异常：{e}"

            answer_for_reply = answer + native_note

            delivered = await self._reply_card_safe(
                incoming_message,
                user_text,
                answer_for_reply
            )

            if not delivered:
                self._reply_markdown_safe(
                    incoming_message,
                    "Jarvis 助手",
                    answer_for_reply
                )
            if task_id and not self.pool.is_failure_response(answer):
                self.task_manager.transition(task_id, COMPLETED, "response delivery completed", result_text=answer_for_reply)

        except Exception as e:
            logger.exception("handle text message failed")
            if task_id:
                try:
                    self.task_manager.transition(task_id, FAILED, "handler exception", error_text=str(e))
                except Exception:
                    logger.exception("task failure state update failed task_id=%s", task_id)
            self._reply_markdown_safe(
                incoming_message,
                "Jarvis 助手异常",
                "处理异常，本次请求已结束，请稍后重试。"
            )
        finally:
            pass


    def _send_native_files_if_any(self, raw, answer):
        paths = find_pptx_paths(answer)

        if not paths:
            return []

        results = []

        for p in paths:
            try:
                result = self.file_sender.send_file_for_raw(raw or {}, p)
                logger.info("native file sent, path=%s result=%s", p, result)
                results.append({
                    "path": p,
                    "ok": True,
                    "result": result,
                })
            except Exception as e:
                logger.exception("send native file failed, path=%s", p)
                results.append({
                    "path": p,
                    "ok": False,
                    "error": str(e),
                })

        return results

    def _send_native_images(self, raw, image_paths):
        results = []
        for image_path in image_paths:
            try:
                result = self.file_sender.send_image_for_raw(raw or {}, image_path)
                logger.info("native image sent, path=%s result=%s", image_path, result)
                results.append({"path": image_path, "ok": True, "result": result})
            except Exception as exc:
                logger.exception("send native image failed, path=%s", image_path)
                results.append({"path": image_path, "ok": False, "error": str(exc)})
        return results


def main():
    if not APP_KEY or not APP_SECRET:
        raise RuntimeError(
            "缺少钉钉凭据：请在 .env 中配置 DINGTALK_APP_KEY/DINGTALK_APP_SECRET，"
            "或保留 DINGTALK_CLIENT_ID/DINGTALK_CLIENT_SECRET。"
        )

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    credential = dingtalk_stream.Credential(APP_KEY, APP_SECRET)
    client = dingtalk_stream.DingTalkStreamClient(credential)

    handler = CodexBotHandler(client)

    from core.recovery_manager import RecoveryManager

    def send_recovery_notice(task, message):
        payload = task.get("payload") or {}
        raw = payload.get("raw") or {}
        handler.file_sender.send_text_for_raw(raw, message)

    recovered = RecoveryManager(handler.task_manager, send_recovery_notice).recover_waiting_restart()
    if recovered:
        logger.info("restart recovery notifications delivered count=%s", recovered)

    client.register_callback_handler(
        dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
        handler
    )

    logger.info("Codex DingTalk image-to-ppt mode started")
    client.start_forever()


if __name__ == "__main__":
    main()
