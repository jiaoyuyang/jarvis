from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class OutputPolicyTest(unittest.TestCase):
    def test_memory_sources_are_internal_by_default(self) -> None:
        skill = (ROOT / "skills/jarvis-memory/SKILL.md").read_text(encoding="utf-8")
        persona = (ROOT / "persona/AGENTS.md").read_text(encoding="utf-8")

        self.assertNotIn("历史事实后标注来源", skill)
        self.assertIn("默认回答不得出现 `【知识库：...】`", skill)
        self.assertIn("只有用户明确要求", skill)
        self.assertIn("知识库检索、记忆读写和来源定位属于内部实现", persona)


if __name__ == "__main__":
    unittest.main()
