from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class PluginContext:
    """Runtime context passed to plugins after the main chain is connected."""

    text: str = ""
    image_paths: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PluginResult:
    handled: bool
    text: str = ""
    files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        handled: bool,
        text: str = "",
        files: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        self.handled = handled
        self.text = text or message or ""
        self.files = list(files or [])
        self.metadata = dict(metadata or {})
        self.data = dict(data or {})

    @property
    def message(self) -> str:
        return self.text


@dataclass(slots=True)
class MessageContext(PluginContext):
    """Sanitized compatibility context used by the pure shadow router."""
    session_id: str = ""
    task_id: str = ""
    msgtype: str = ""
    sender_id: str = ""
    conversation_id: str = ""
    local_file_path: str = ""
    filename: str = ""
    mime_type: str = ""
    current_image_path: str = ""
    recent_image_path: str = ""
    has_attachment: bool = False
    word_context: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(cls, *, text: str, raw: dict[str, Any], session_id: str, task_id: str,
                    msgtype: str, image_paths: list[str] | None = None) -> "MessageContext":
        source = raw if isinstance(raw, dict) else {}
        content = source.get("content") if isinstance(source.get("content"), dict) else {}
        file_info = source.get("file") if isinstance(source.get("file"), dict) else {}
        filename = str(file_info.get("fileName") or content.get("fileName") or source.get("fileName") or "")
        mime_type = str(file_info.get("mimeType") or content.get("mimeType") or source.get("mimeType") or "")
        paths = list(image_paths or [])
        safe_raw = {"msgtype": str(source.get("msgtype") or ""),
                    "conversationType": str(source.get("conversationType") or ""),
                    "has_attachment": bool(paths or filename or source.get("downloadCode") or content.get("downloadCode")),
                    "filename": filename, "mime_type": mime_type}
        return cls(
            text=text or "", image_paths=paths, metadata={"raw": safe_raw}, session_id=session_id or "", task_id=task_id or "",
            msgtype=msgtype or str(source.get("msgtype") or ""), sender_id=str(source.get("senderId") or ""),
            conversation_id=str(source.get("conversationId") or source.get("conversation_id") or ""),
            local_file_path=str(source.get("local_file_path") or ""), filename=filename, mime_type=mime_type,
            current_image_path=paths[-1] if paths else "", recent_image_path=paths[-2] if len(paths) > 1 else "",
            has_attachment=bool(paths or filename or source.get("downloadCode") or content.get("downloadCode")),
            word_context=str(source.get("word_context") or ""),
            raw=safe_raw,
        )


@dataclass(frozen=True, slots=True)
class RouteDecision:
    plugin: str
    route: str
    handled: bool
    action: str
    reason: str
    priority: int
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    evaluated_plugins: tuple[dict[str, Any], ...] = ()


class Plugin(Protocol):
    name: str
    priority: int

    def match(self, context: PluginContext) -> bool:
        ...

    def handle(self, context: PluginContext) -> PluginResult:
        ...


class BasePlugin:
    name = "base"
    priority = 1000

    def match(self, context: PluginContext) -> bool:
        return False

    def handle(self, context: PluginContext) -> PluginResult:
        return PluginResult(
            handled=False,
            message=f"{self.name} 未接入主链路",
        )
