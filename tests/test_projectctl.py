from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills/jarvis-project/scripts/projectctl.py"


class ProjectCtlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.run_cli("init", "--project", "jarvis", "--name", "Jarvis")

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
            self.fail(result.stderr)
        return result

    def record_action(self) -> str:
        result = self.run_cli(
            "record",
            "--project",
            "jarvis",
            "--kind",
            "action",
            "--title",
            "Deploy skills",
            "--content",
            "Deploy workflow skills",
            "--owner",
            "Jarvis",
            "--due",
            "2026-08-11",
            "--source",
            "confirmed conversation",
        )
        return result.stdout.split("recorded=", 1)[1].split()[0]

    def test_records_and_renders_action(self) -> None:
        item_id = self.record_action()
        actions = (self.workspace / "knowledge/projects/jarvis/ACTIONS.md").read_text()
        self.assertIn("Deploy workflow skills", actions)
        self.assertIn("Jarvis", actions)
        self.run_cli("verify", "--project", "jarvis", "--rebuild")
        ledger = self.workspace / "knowledge/projects/jarvis/ledger.jsonl"
        self.assertEqual(json.loads(ledger.read_text().splitlines()[0])["item"]["id"], item_id)

    def test_duplicate_and_change_keep_history(self) -> None:
        item_id = self.record_action()
        duplicate = self.run_cli(
            "record",
            "--project",
            "jarvis",
            "--kind",
            "action",
            "--title",
            "Deploy skills",
            "--content",
            "Deploy workflow skills",
            "--owner",
            "Jarvis",
            "--due",
            "2026-08-11",
            "--source",
            "confirmed conversation",
        )
        self.assertIn(f"duplicate={item_id}", duplicate.stdout)
        self.run_cli("change", item_id, "--status", "done", "--note", "verified")
        actions = (self.workspace / "knowledge/projects/jarvis/ACTIONS.md").read_text()
        self.assertIn("## 已关闭", actions)
        self.assertIn("done", actions)
        self.assertEqual(
            len((self.workspace / "knowledge/projects/jarvis/ledger.jsonl").read_text().splitlines()),
            2,
        )

    def test_rejects_invalid_due_date(self) -> None:
        result = self.run_cli(
            "record",
            "--project",
            "jarvis",
            "--kind",
            "action",
            "--title",
            "Bad due",
            "--content",
            "Invalid date",
            "--due",
            "tomorrow",
            "--source",
            "test",
            ok=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("YYYY-MM-DD", result.stderr)


if __name__ == "__main__":
    unittest.main()
