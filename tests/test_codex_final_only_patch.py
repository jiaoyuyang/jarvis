import asyncio
import importlib.util
from pathlib import Path
import py_compile
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
PATCH = ROOT / "patches/patch_qwenpaw_codex_final_only.py"


def load_patch():
    spec = importlib.util.spec_from_file_location("codex_final_only_patch", PATCH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def adapter_fixture(module) -> str:
    return (
        '''import json
from dataclasses import dataclass


class HarnessEventKind:
    TEXT_DELTA = "text_delta"
    COMPLETED = "completed"


@dataclass
class HarnessEvent:
    kind: str
    text: str = ""
    item_id: str = ""


class Adapter:
    @staticmethod
    def _convert_notification(message):
        if message.get("method") == "turn/completed":
            return HarnessEvent(kind=HarnessEventKind.COMPLETED)
        return None

    async def stream(self, client, settings):
'''
        + module.STATE_ANCHOR
        + '''            async for message in queue:
                params = message.get("params") or {}
'''
        + module.LOOP_ANCHOR
        + '''        finally:
            pass
'''
    )


class FakeClient:
    def __init__(self, messages):
        self.messages = messages

    async def _items(self):
        for message in self.messages:
            yield message

    def subscribe(self):
        return self._items()


class CodexFinalOnlyPatchTest(unittest.TestCase):
    def setUp(self):
        self.module = load_patch()

    def test_tool_artifact_is_appended_to_final_message(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter_path = Path(temp) / "adapter.py"
            adapter_path.write_text(adapter_fixture(self.module), encoding="utf-8")
            self.module.patch(adapter_path)
            py_compile.compile(str(adapter_path), doraise=True)
            spec = importlib.util.spec_from_file_location("patched_adapter", adapter_path)
            patched = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(patched)

            messages = [
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "commandExecution",
                            "aggregatedOutput": (
                                '{"status":"ok","output":'
                                '"file:///app/working/weather_chart.png"}'
                            ),
                        }
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "id": "final",
                            "text": "天气趋势图如下。",
                        }
                    },
                },
                {"method": "turn/completed", "params": {"turn": {}}},
            ]

            async def collect():
                return [
                    event
                    async for event in patched.Adapter().stream(
                        FakeClient(messages),
                        {"final_only": True},
                    )
                ]

            events = asyncio.run(collect())

        text_events = [e for e in events if e.kind == "text_delta"]
        self.assertEqual(len(text_events), 1)
        self.assertIn("天气趋势图如下", text_events[0].text)
        self.assertIn(
            "[生成的图表](file:///app/working/weather_chart.png)",
            text_events[0].text,
        )

    def test_unsafe_or_failed_artifact_is_not_appended(self):
        replacement = self.module.LOOP_REPLACEMENT
        self.assertIn('artifact_url.startswith("file:///app/working/")', replacement)
        self.assertIn('output_payload.get("status") == "ok"', replacement)
        self.assertNotIn("sandbox=danger-full-access", replacement)


if __name__ == "__main__":
    unittest.main()
