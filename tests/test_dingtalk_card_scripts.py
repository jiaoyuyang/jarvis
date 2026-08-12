from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class DingTalkCardScriptsTest(unittest.TestCase):
    def test_shell_scripts_are_valid(self) -> None:
        for name in ("enable-dingtalk-card.sh", "disable-dingtalk-card.sh"):
            subprocess.run(
                ["bash", "-n", str(ROOT / "scripts" / name)],
                check=True,
            )

    def test_card_mode_preserves_final_only_delivery(self) -> None:
        script = (ROOT / "scripts/enable-dingtalk-card.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('dingtalk["message_type"] = "card"', script)
        self.assertIn('dingtalk["cron_message_type"] = "card"', script)
        self.assertIn('dingtalk["card_template_key"] = "content"', script)
        self.assertIn('dingtalk["streaming_enabled"] = False', script)
        self.assertIn('dingtalk["show_thinking"] = False', script)
        self.assertNotIn("d418dfbd-8251-4e7f-921f-4791adb61727", script)

    def test_markdown_rollback_is_available(self) -> None:
        script = (ROOT / "scripts/disable-dingtalk-card.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('dingtalk["message_type"] = "markdown"', script)
        self.assertIn('dingtalk["streaming_enabled"] = False', script)


if __name__ == "__main__":
    unittest.main()
