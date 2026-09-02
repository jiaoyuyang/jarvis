import importlib.util
from pathlib import Path
import py_compile
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
REQUEST_PATCH = (
    ROOT / "patches" / "patch_qwenpaw_capability_request_gate.py"
)
RESPONSE_PATCH = (
    ROOT / "patches" / "patch_qwenpaw_capability_response_guard.py"
)
FINAL_ONLY_PATCH = ROOT / "patches" / "patch_qwenpaw_codex_final_only.py"
TIMEOUT_PATCH = ROOT / "patches" / "patch_qwenpaw_codex_turn_timeout.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CapabilityRequestClassificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("capability_request_gate", REQUEST_PATCH)

    def test_rejects_general_generation_without_starting_codex(self) -> None:
        blocked = (
            "帮我生成一张湘军攻打太平天国的图片",
            "用 image2 生成安庆围城场景",
            "制作一张历史题材海报",
            "画个湘军围攻安庆的场景",
            "生成一张系统应用架构图",
            "生成一张包含折线图的营销海报",
            "draw an illustration of a historical siege",
        )
        for query in blocked:
            with self.subTest(query=query):
                self.assertTrue(
                    self.module.is_unsupported_image_request(query)
                )

    def test_rejects_reference_edit_but_allows_image_analysis(self) -> None:
        self.assertTrue(
            self.module.is_unsupported_image_request(
                "把这张图改成水墨风格",
                has_image_input=True,
            )
        )
        self.assertFalse(
            self.module.is_unsupported_image_request(
                "分析这张图片里有哪些内容",
                has_image_input=True,
            )
        )
        self.assertFalse(
            self.module.is_unsupported_image_request(
                "Jarvis 可以用 image2 吗？"
            )
        )

    def test_allows_deterministic_chart_requests(self) -> None:
        allowed = (
            "生成一张最近七天温度趋势图",
            "把这些数据制作成折线图",
            "create a bar chart from this data",
        )
        for query in allowed:
            with self.subTest(query=query):
                self.assertFalse(
                    self.module.is_unsupported_image_request(query)
                )

    def test_request_patch_is_strict_and_idempotent(self) -> None:
        source = "\n".join(
            (self.module.HELPER_ANCHOR, self.module.CALL_ANCHOR)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "base.py"
            path.write_text(source, encoding="utf-8")
            self.module.patch(path)
            first = path.read_text(encoding="utf-8")
            self.module.patch(path)
            second = path.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertIn(self.module.MARKER, first)
        self.assertIn("await self._jarvis_capability_gate(payload)", first)
        self.assertLess(
            first.index("await self._jarvis_capability_gate(payload)"),
            first.index("if self._workspace is not None"),
        )


class CapabilityResponseGuardTest(unittest.TestCase):
    def test_composes_after_final_only_and_timeout_patches(self) -> None:
        final_only = load_module("capability_final_only", FINAL_ONLY_PATCH)
        timeout = load_module("capability_timeout", TIMEOUT_PATCH)
        guard = load_module("capability_response_guard", RESPONSE_PATCH)
        fixture = (
            "import asyncio\nimport json\n\n"
            "class CodexAppServerError(Exception):\n    pass\n\n"
            "class Adapter:\n"
            "    async def stream(self, session_id, settings, cwd):\n"
            + timeout.START_ANCHOR
            + final_only.STATE_ANCHOR
            + "            params = {}\n"
            + timeout.TURN_START_ANCHOR
            + timeout.WAIT_ANCHOR
            + '                params = message.get("params") or {}\n'
            + final_only.LOOP_ANCHOR
            + timeout.ERROR_ANCHOR
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "adapter.py"
            path.write_text(fixture, encoding="utf-8")
            final_only.patch(path)
            timeout.patch(path)
            guard.patch(path)
            first = path.read_text(encoding="utf-8")
            guard.patch(path)
            second = path.read_text(encoding="utf-8")
            py_compile.compile(str(path), doraise=True)

        self.assertEqual(first, second)
        self.assertIn(guard.MARKER, first)
        self.assertIn("unsupported_capability_claims", first)
        self.assertIn("not buffered_artifact_links", first)
        self.assertIn("当前环境不支持普通图片生成", first)


class CapabilityRuntimeWiringTest(unittest.TestCase):
    def test_docker_and_status_require_both_guards(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        status = (ROOT / "scripts/codex-status.sh").read_text(
            encoding="utf-8"
        )
        regression = (ROOT / "scripts/regression.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("patch_qwenpaw_capability_request_gate.py", dockerfile)
        self.assertIn("patch_qwenpaw_capability_response_guard.py", dockerfile)
        self.assertIn("capability_request_gate=", status)
        self.assertIn("capability_response_guard=", status)
        self.assertIn("generic_image_generation=unsupported", status)
        self.assertIn("capability_request_gate=installed", regression)
        self.assertIn("capability_response_guard=installed", regression)


if __name__ == "__main__":
    unittest.main()
