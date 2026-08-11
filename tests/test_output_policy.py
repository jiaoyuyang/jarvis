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

    def test_presentation_layer_is_semantic_and_dingtalk_compatible(self) -> None:
        skill = (ROOT / "skills/jarvis-presentation/SKILL.md").read_text(
            encoding="utf-8"
        )
        persona = (ROOT / "persona/AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("简单问题用一至三段自然语言", skill)
        self.assertIn("没有对应语义时不用", skill)
        self.assertIn("不能根据主观感觉估算百分比", skill)
        self.assertIn("不使用 Mermaid、HTML", skill)
        self.assertIn("所有最终答复遵循 `jarvis-presentation`", persona)

    def test_turn_closure_does_not_store_every_reply(self) -> None:
        persona = (ROOT / "persona/AGENTS.md").read_text(encoding="utf-8")
        memory = (ROOT / "skills/jarvis-memory/SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("没有候选时不写文件、不调用工具", persona)
        self.assertIn("最终答复文本本身不作为", persona)
        self.assertIn("没有候选时不调用工具、不创建", memory)

    def test_project_routing_requires_confirmation_on_conflict(self) -> None:
        project = (ROOT / "skills/jarvis-project/SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("`jarvis` 只用于 Jarvis 智能体自身", project)
        self.assertIn("不要静默登记", project)
        self.assertIn("使用迁移，不删除或直接改写账本", project)


if __name__ == "__main__":
    unittest.main()
