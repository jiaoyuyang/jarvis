import importlib.util
from pathlib import Path
import py_compile
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
FINAL_ONLY_PATCH = ROOT / "patches/patch_qwenpaw_codex_final_only.py"
TIMEOUT_PATCH = ROOT / "patches/patch_qwenpaw_codex_turn_timeout.py"


def load_patch(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def adapter_fixture(final_only, timeout) -> str:
    return (
        '''import asyncio
import json


class CodexAppServerError(Exception):
    pass


class Adapter:
    async def stream(self, session_id, settings, cwd):
'''
        + timeout.START_ANCHOR
        + final_only.STATE_ANCHOR
        + '''            params = {}
'''
        + timeout.TURN_START_ANCHOR
        + timeout.WAIT_ANCHOR
        + '''                params = message.get("params") or {}
'''
        + final_only.LOOP_ANCHOR
        + timeout.ERROR_ANCHOR
    )


class CodexPatchCompositionTest(unittest.TestCase):
    def test_dockerfile_patch_order_compiles(self):
        final_only = load_patch("codex_final_only_patch", FINAL_ONLY_PATCH)
        timeout = load_patch("codex_timeout_patch", TIMEOUT_PATCH)

        with tempfile.TemporaryDirectory() as temp:
            adapter_path = Path(temp) / "adapter.py"
            adapter_path.write_text(
                adapter_fixture(final_only, timeout),
                encoding="utf-8",
            )

            final_only.patch(adapter_path)
            timeout.patch(adapter_path)
            py_compile.compile(str(adapter_path), doraise=True)
            patched = adapter_path.read_text(encoding="utf-8")

        self.assertIn(final_only.MARKER, patched)
        self.assertIn(timeout.MARKER, patched)
        self.assertEqual(patched.count("buffered_artifact_links"), 4)


if __name__ == "__main__":
    unittest.main()
