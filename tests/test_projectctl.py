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

    def test_move_preserves_history_and_removes_item_from_current_view(self) -> None:
        item_id = self.record_action()
        result = self.run_cli(
            "move",
            item_id,
            "--project",
            "jarvis",
            "--to-project",
            "wanfuo",
            "--to-name",
            "万佛用户增长平台",
            "--reason",
            "验收材料目标项目选择错误",
        )
        self.assertIn(f"moved={item_id}", result.stdout)

        source_actions = (
            self.workspace / "knowledge/projects/jarvis/ACTIONS.md"
        ).read_text()
        source_timeline = (
            self.workspace / "knowledge/projects/jarvis/TIMELINE.md"
        ).read_text()
        target_actions = (
            self.workspace / "knowledge/projects/wanfuo/ACTIONS.md"
        ).read_text()
        self.assertNotIn("Deploy workflow skills", source_actions)
        self.assertIn("｜migration｜moved｜", source_timeline)
        self.assertIn("已迁移至 wanfuo", source_timeline)
        self.assertIn("Deploy workflow skills", target_actions)

        duplicate = self.run_cli(
            "move",
            item_id,
            "--project",
            "jarvis",
            "--to-project",
            "wanfuo",
            "--to-name",
            "万佛用户增长平台",
            "--reason",
            "重复执行",
        )
        self.assertIn(f"already_moved={item_id}", duplicate.stdout)
        self.assertEqual(
            len(
                (
                    self.workspace / "knowledge/projects/wanfuo/ledger.jsonl"
                ).read_text().splitlines()
            ),
            1,
        )

        rejected = self.run_cli(
            "change",
            item_id,
            "--status",
            "done",
            "--note",
            "不应修改原项目迁出条目",
            ok=False,
        )
        self.assertIn("已迁移条目", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
