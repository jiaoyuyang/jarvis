import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodexTurnTimeoutPatchTest(unittest.TestCase):
    def test_patch_is_strict_idempotent_and_interrupts(self) -> None:
        module = load_module(
            "codex_turn_timeout_patch",
            ROOT / "patches" / "patch_qwenpaw_codex_turn_timeout.py",
        )
        source = "\n".join(anchor for anchor, _ in module.REPLACEMENTS)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "adapter.py"
            path.write_text(source, encoding="utf-8")
            module.patch(path)
            first = path.read_text(encoding="utf-8")
            module.patch(path)
            second = path.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertIn(module.MARKER, first)
        self.assertIn("turn_deadline", first)
        self.assertIn("asyncio.wait_for", first)
        self.assertIn("await self._interrupt_turn", first)
        self.assertIn("600", first)


class StopCommandPatchTest(unittest.TestCase):
    def test_stop_bypasses_codex_and_calls_native_handler(self) -> None:
        module = load_module(
            "stop_command_patch",
            ROOT / "patches" / "patch_qwenpaw_stop_command.py",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "base.py"
            path.write_text(module.ANCHOR, encoding="utf-8")
            module.patch(path)
            first = path.read_text(encoding="utf-8")
            module.patch(path)
            second = path.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertIn(module.MARKER, first)
        self.assertIn('command_token == "/stop"', first)
        self.assertIn("handle_control_command", first)
        self.assertIn("await self.send", first)
        self.assertIn("await self._on_process_completed", first)


class ReliabilityScriptsTest(unittest.TestCase):
    def test_recovery_and_upgrade_scripts_are_valid(self) -> None:
        recovery = ROOT / "scripts" / "recover-codex-session.sh"
        upgrade = ROOT / "scripts" / "upgrade-reliability-v1.sh"
        subprocess.run(["bash", "-n", str(recovery)], check=True)
        subprocess.run(["bash", "-n", str(upgrade)], check=True)

        recovery_text = recovery.read_text(encoding="utf-8")
        self.assertIn("./scripts/backup.sh", recovery_text)
        self.assertIn("data.pop(session_id", recovery_text)
        self.assertIn("docker compose stop jarvis", recovery_text)
        self.assertIn("docker compose up -d jarvis", recovery_text)

        upgrade_text = upgrade.read_text(encoding="utf-8")
        self.assertIn("QWENPAW_IMAGE=jarvis:qwenpaw-2.1-codex", upgrade_text)
        self.assertIn("turn_timeout_seconds", upgrade_text)
        self.assertIn("reliability_v1=active", upgrade_text)


if __name__ == "__main__":
    unittest.main()
