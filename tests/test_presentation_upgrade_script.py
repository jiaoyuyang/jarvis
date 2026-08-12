from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class PresentationUpgradeScriptTest(unittest.TestCase):
    def test_upgrade_script_is_valid_and_recoverable(self) -> None:
        script_path = ROOT / "scripts/upgrade-presentation-v2.sh"
        subprocess.run(["bash", "-n", str(script_path)], check=True)
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("./scripts/backup.sh", script)
        self.assertIn("./scripts/apply-persona.sh", script)
        self.assertIn("./scripts/harden-security.sh", script)
        self.assertIn("./scripts/workflow-status.sh", script)
        self.assertIn("./scripts/codex-status.sh", script)
        self.assertIn("presentation_v2=active", script)


if __name__ == "__main__":
    unittest.main()
