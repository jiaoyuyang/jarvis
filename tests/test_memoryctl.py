from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills/jarvis-memory/scripts/memoryctl.py"


class MemoryCtlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["python3", str(SCRIPT), "--workspace", str(self.workspace), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        if ok and result.returncode != 0:
            self.fail(f"command failed: {result.stderr}")
        return result

    def state(self) -> dict:
        return json.loads((self.workspace / "memory/state.json").read_text())

    def remember_name(self, name: str = "焦书记") -> str:
        result = self.run_cli(
            "remember",
            "--type",
            "fact",
            "--category",
            "profile",
            "--key",
            "profile.preferred_name",
            "--content",
            f"用户常用称呼是{name}",
            "--source",
            "钉钉当前会话",
        )
        return result.stdout.split("remembered=", 1)[1].split()[0]

    def test_capture_and_promote(self) -> None:
        result = self.run_cli(
            "capture",
            "--type",
            "decision",
            "--key",
            "project.jarvis.memory_v1",
            "--content",
            "Jarvis采用两级记忆",
            "--source",
            "钉钉当前会话",
        )
        memory_id = result.stdout.split("captured=", 1)[1].split()[0]
        self.assertEqual(self.state()["memories"][memory_id]["status"], "pending")
        self.run_cli("promote", memory_id, "--category", "decisions")
        self.assertEqual(self.state()["memories"][memory_id]["status"], "active")
        curated = (self.workspace / "memory/curated/decisions.md").read_text()
        self.assertIn("Jarvis采用两级记忆", curated)

    def test_duplicate_is_idempotent(self) -> None:
        first = self.remember_name()
        result = self.run_cli(
            "remember",
            "--type",
            "fact",
            "--category",
            "profile",
            "--key",
            "profile.preferred_name",
            "--content",
            "用户常用称呼是焦书记",
            "--source",
            "另一会话",
        )
        self.assertIn(f"duplicate={first}", result.stdout)
        self.assertEqual(len(self.state()["memories"]), 1)

    def test_conflict_requires_correction_and_keeps_history(self) -> None:
        old_id = self.remember_name()
        conflict = self.run_cli(
            "remember",
            "--type",
            "fact",
            "--category",
            "profile",
            "--key",
            "profile.preferred_name",
            "--content",
            "用户常用称呼是焦总",
            "--source",
            "钉钉当前会话",
            ok=False,
        )
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("已有有效版本", conflict.stderr)
        result = self.run_cli(
            "correct",
            old_id,
            "--content",
            "用户常用称呼是焦总",
            "--source",
            "用户明确更正",
            "--reason",
            "称呼偏好发生变化",
        )
        new_id = result.stdout.split("replacement=", 1)[1].strip()
        memories = self.state()["memories"]
        self.assertEqual(memories[old_id]["status"], "superseded")
        self.assertEqual(memories[new_id]["status"], "active")
        curated = (self.workspace / "memory/curated/profile.md").read_text()
        self.assertNotIn("用户常用称呼是焦书记", curated)
        self.assertIn("用户常用称呼是焦总", curated)
        ledger = (self.workspace / "memory/ledger.jsonl").read_text()
        self.assertIn(old_id, ledger)
        self.assertIn(new_id, ledger)

    def test_retire_removes_only_active_projection(self) -> None:
        memory_id = self.remember_name()
        self.run_cli("retire", memory_id, "--reason", "用户要求停止使用")
        self.assertEqual(self.state()["memories"][memory_id]["status"], "retired")
        curated = (self.workspace / "memory/curated/profile.md").read_text()
        self.assertNotIn("用户常用称呼是焦书记", curated)
        self.assertIn(memory_id, (self.workspace / "memory/ledger.jsonl").read_text())

    def test_sensitive_data_is_rejected(self) -> None:
        result = self.run_cli(
            "capture",
            "--type",
            "fact",
            "--key",
            "credential.test",
            "--content",
            "password: VerySecret123",
            "--source",
            "聊天",
            ok=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("拒绝写入", result.stderr)
        self.assertFalse((self.workspace / "memory/ledger.jsonl").exists())

    def test_verify_and_rebuild(self) -> None:
        self.remember_name()
        (self.workspace / "memory/curated/profile.md").unlink()
        self.run_cli("verify", "--rebuild")
        self.assertTrue((self.workspace / "memory/curated/profile.md").exists())


if __name__ == "__main__":
    unittest.main()
