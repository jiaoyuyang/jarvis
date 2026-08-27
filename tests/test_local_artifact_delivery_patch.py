import asyncio
import importlib.util
import os
from pathlib import Path
import py_compile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def renderer_fixture(module) -> str:
    return (
        module.RENDERER_IMPORT_ANCHOR
        + '''

class ImageContent:
    def __init__(self, image_url):
        self.image_url = image_url


class FileContent:
    def __init__(self, file_url, filename):
        self.file_url = file_url
        self.filename = filename


_OutgoingPart = Union[ImageContent, FileContent]


class MessageRenderer:
    def message_to_parts(self, content, strip_headline=lambda value: value):
        result = []
        for c in content:
            ctype = getattr(c, "type", None)
'''
        + module.RENDERER_TEXT_ANCHOR
        + '''        return result
'''
    )


def dingtalk_fixture(module) -> str:
    return (
        '''import logging
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


class ContentType:
    IMAGE = "image"
    FILE = "file"
    VIDEO = "video"
    AUDIO = "audio"


class DingTalkChannel:
    def __init__(self):
        self.sent_payloads = []
        self.open_api_parts = []

    def _is_public_http_url(self, value):
        return isinstance(value, str) and value.startswith(("http://", "https://"))

    async def _resolve_open_api_params_from_handle(self, to_handle, meta):
        return {
            "conversation_id": meta.get("conversation_id", "cid"),
            "conversation_type": meta.get("conversation_type", "group"),
            "sender_staff_id": meta.get("sender_staff_id", "staff"),
        }

    async def _send_media_part_via_open_api(self, part, **params):
        self.open_api_parts.append((part, params))
        return True

    async def _send_payload_via_session_webhook(self, webhook, payload):
        self.sent_payloads.append((webhook, payload))
        return True

    async def _send_media_part_via_webhook(
        self,
        session_webhook,
        part,
    ):
        upload_type = "image"
        filename = "chart.png"
        ext = "png"
        media_id = "@generated_chart"
        if media_id:
'''
        + module.DINGTALK_EXISTING_IMAGE_MEDIA_ANCHOR
        + '''

    async def _send_uploaded_image_media_id(
        self,
        session_webhook,
        part,
    ):
        upload_type = "image"
        filename = "chart.png"
        ext = "png"
        media_id = "@uploaded_chart"
'''
        + module.DINGTALK_UPLOADED_IMAGE_MEDIA_ANCHOR
        + '''

    async def _open_api_uploaded_media(self):
        effective_upload_type = "image"
        media_id = "@open_api_chart"
        filename = "chart.png"
        ext = "png"
        conversation_id = "cid"
        conversation_type = "group"
        sender_staff_id = "staff"
'''
        + module.OPEN_API_UPLOADED_MEDIA_ANCHOR
        + '''

    async def _send_open_api_message(self, **payload):
        self.sent_payloads.append(("open_api", payload))
        return True
'''
        + module.DINGTALK_ANCHOR
    )


class LocalArtifactDeliveryPatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module(
            "local_artifact_delivery_patch",
            ROOT / "patches" / "patch_qwenpaw_local_artifact_delivery.py",
        )

    def test_patch_is_strict_idempotent_and_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            renderer = root / "renderer.py"
            dingtalk = root / "channel.py"
            renderer.write_text(renderer_fixture(self.module), encoding="utf-8")
            dingtalk.write_text(dingtalk_fixture(self.module), encoding="utf-8")

            self.module.patch_renderer(renderer)
            self.module.patch_dingtalk(dingtalk)
            first_renderer = renderer.read_text(encoding="utf-8")
            first_dingtalk = dingtalk.read_text(encoding="utf-8")
            self.module.patch_renderer(renderer)
            self.module.patch_dingtalk(dingtalk)

            self.assertEqual(first_renderer, renderer.read_text(encoding="utf-8"))
            self.assertEqual(first_dingtalk, dingtalk.read_text(encoding="utf-8"))
            py_compile.compile(str(renderer), doraise=True)
            py_compile.compile(str(dingtalk), doraise=True)

        self.assertIn(self.module.RENDERER_MARKER, first_renderer)
        self.assertIn("_extract_local_artifacts", first_renderer)
        self.assertIn(self.module.DINGTALK_MARKER, first_dingtalk)
        self.assertIn("failed_count", first_dingtalk)
        self.assertIn("发送到钉钉失败", first_dingtalk)
        self.assertNotIn("media_id for inline image preview", first_dingtalk)
        self.assertEqual(first_dingtalk.count('"msgtype": "image"'), 2)
        self.assertEqual(first_dingtalk.count('"media_id": media_id'), 2)
        self.assertIn('msg_key="sampleImageMsg"', first_dingtalk)
        self.assertIn('msg_param={"photoURL": media_id}', first_dingtalk)

    def test_uploaded_image_uses_native_dingtalk_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dingtalk = Path(temp_dir) / "channel.py"
            dingtalk.write_text(dingtalk_fixture(self.module), encoding="utf-8")
            self.module.patch_dingtalk(dingtalk)
            patched = load_module("patched_dingtalk_native_image", dingtalk)
            channel = patched.DingTalkChannel()
            result = asyncio.run(
                channel._send_uploaded_image_media_id(
                    "https://oapi.dingtalk.com/robot/sendBySession",
                    SimpleNamespace(type=patched.ContentType.IMAGE),
                ),
            )

        self.assertTrue(result)
        self.assertEqual(
            channel.sent_payloads,
            [
                (
                    "https://oapi.dingtalk.com/robot/sendBySession",
                    {
                        "msgtype": "image",
                        "image": {"media_id": "@uploaded_chart"},
                    },
                ),
            ],
        )

    def test_image_link_becomes_one_uploadable_media_part(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            renderer = root / "renderer.py"
            renderer.write_text(renderer_fixture(self.module), encoding="utf-8")
            self.module.patch_renderer(renderer)
            patched = load_module("patched_renderer", renderer)
            chart = root / "weather.png"
            chart.write_bytes(b"not-a-real-png-but-an-existing-test-artifact")
            text = (
                f"图表如下：[天气趋势](file://{chart})\n"
                f"重复引用：![天气趋势](sandbox:{chart})"
            )
            with mock.patch.dict(
                os.environ,
                {"QWENPAW_WORKING_DIR": str(root)},
                clear=False,
            ):
                clean, parts = patched._extract_local_artifacts(text)

        self.assertNotIn("file://", clean)
        self.assertNotIn("sandbox:", clean)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].image_url, chart.as_uri())
        self.assertEqual(clean.count("见下方图片"), 2)

    def test_local_image_bypasses_webhook_and_uses_open_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dingtalk = Path(temp_dir) / "channel.py"
            dingtalk.write_text(dingtalk_fixture(self.module), encoding="utf-8")
            self.module.patch_dingtalk(dingtalk)
            patched = load_module("patched_dingtalk_open_api_route", dingtalk)
            channel = patched.DingTalkChannel()
            part = SimpleNamespace(
                type=patched.ContentType.IMAGE,
                image_url="file:///app/working/weather_chart.png",
            )
            asyncio.run(
                channel._deliver_media_parts(
                    [part],
                    "https://oapi.dingtalk.com/robot/sendBySession",
                    "dingtalk:sw:test",
                    {"conversation_id": "cid"},
                )
            )

        self.assertEqual(channel.sent_payloads, [])
        self.assertEqual(len(channel.open_api_parts), 1)
        self.assertIs(channel.open_api_parts[0][0], part)

    def test_open_api_uploaded_image_uses_sample_image_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dingtalk = Path(temp_dir) / "channel.py"
            dingtalk.write_text(dingtalk_fixture(self.module), encoding="utf-8")
            self.module.patch_dingtalk(dingtalk)
            patched = load_module("patched_dingtalk_sample_image", dingtalk)
            channel = patched.DingTalkChannel()
            asyncio.run(channel._open_api_uploaded_media())

        self.assertEqual(
            channel.sent_payloads,
            [
                (
                    "open_api",
                    {
                        "msg_key": "sampleImageMsg",
                        "msg_param": {"photoURL": "@open_api_chart"},
                        "conversation_id": "cid",
                        "conversation_type": "group",
                        "sender_staff_id": "staff",
                    },
                )
            ],
        )

    def test_outside_or_unsupported_file_is_never_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            working = base / "working"
            working.mkdir()
            outside = base / "secret.png"
            outside.write_bytes(b"secret")
            unsupported = working / "script.py"
            unsupported.write_text("print('no')", encoding="utf-8")
            renderer = base / "renderer.py"
            renderer.write_text(renderer_fixture(self.module), encoding="utf-8")
            self.module.patch_renderer(renderer)
            patched = load_module("patched_renderer_reject", renderer)
            with mock.patch.dict(
                os.environ,
                {"QWENPAW_WORKING_DIR": str(working)},
                clear=False,
            ):
                clean, parts = patched._extract_local_artifacts(
                    f"[外部](file://{outside}) [脚本](file://{unsupported})",
                )

        self.assertEqual(parts, [])
        self.assertNotIn(str(outside), clean)
        self.assertNotIn(str(unsupported), clean)
        self.assertIn("本地文件不可用", clean)
        self.assertIn("不支持的文件类型", clean)


if __name__ == "__main__":
    unittest.main()
