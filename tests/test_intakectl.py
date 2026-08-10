from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills/jarvis-intake/scripts/intakectl.py"


class IntakeCtlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / "media").mkdir()
        self.source = self.workspace / "media/meeting.txt"
        self.source.write_text("meeting decision", encoding="utf-8")

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

    def register(self) -> str:
        result = self.run_cli(
            "register",
            "--source",
            "media/meeting.txt",
            "--project",
            "enterprise-architecture",
            "--kind",
            "meeting",
            "--title",
            "Weekly meeting",
            "--source-label",
            "DingTalk upload",
        )
        return result.stdout.split("registered=", 1)[1].split()[0]

    def test_register_verify_and_deduplicate(self) -> None:
        material_id = self.register()
        ledger = self.workspace / "knowledge/intake/ledger.jsonl"
        record = json.loads(ledger.read_text(encoding="utf-8").strip())
        self.assertEqual(record["id"], material_id)
        archived = self.workspace / record["archive"]
        self.assertEqual(archived.read_text(encoding="utf-8"), "meeting decision")
        duplicate = self.run_cli(
            "register",
            "--source",
            "media/meeting.txt",
            "--project",
            "enterprise-architecture",
            "--kind",
            "meeting",
            "--title",
            "Weekly meeting",
            "--source-label",
            "DingTalk upload",
        )
        self.assertIn(f"duplicate={material_id}", duplicate.stdout)
        self.run_cli("verify")

    def test_rejects_path_outside_media(self) -> None:
        outside = self.workspace / "secret.txt"
        outside.write_text("no", encoding="utf-8")
        result = self.run_cli(
            "register",
            "--source",
            "secret.txt",
            "--project",
            "inbox",
            "--kind",
            "other",
            "--title",
            "bad",
            "--source-label",
            "test",
            ok=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("只允许归档", result.stderr)

    def test_rejects_symlink(self) -> None:
        target = self.workspace / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        (self.workspace / "media/link.txt").symlink_to(target)
        result = self.run_cli(
            "register",
            "--source",
            "media/link.txt",
            "--project",
            "inbox",
            "--kind",
            "other",
            "--title",
            "link",
            "--source-label",
            "test",
            ok=False,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
