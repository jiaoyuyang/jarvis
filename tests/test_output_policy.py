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

    def test_presentation_v2_controls_card_density(self) -> None:
        skill = (ROOT / "skills/jarvis-presentation/SKILL.md").read_text(
            encoding="utf-8"
        )
        persona = (ROOT / "persona/AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("版本：2.2", skill)
        self.assertIn("结论层", skill)
        self.assertIn("摘要层", skill)
        self.assertIn("行动层", skill)
        self.assertIn("默认最多四个内容区块", skill)
        self.assertIn("固定四维速览外，每个区块最多三个要点", skill)
        self.assertIn("600—900 个汉字", skill)
        self.assertIn("不使用一级 Markdown 标题", skill)
        self.assertIn("表格单元格使用纯文本", skill)
        self.assertIn("结论—摘要—行动", persona)
        self.assertIn("第一屏必须看到核心判断", persona)

    def test_chart_delivery_requires_a_real_local_artifact(self) -> None:
        skill = (ROOT / "skills/jarvis-presentation/SKILL.md").read_text(
            encoding="utf-8"
        )
        chart_skill = (ROOT / "skills/jarvis-chart/SKILL.md").read_text(
            encoding="utf-8"
        )
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        status_script = (ROOT / "scripts/codex-status.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("版本：2.2", skill)
        self.assertIn("file:///app/working/weather_trend.png", skill)
        self.assertIn("图表生成失败", skill)
        self.assertIn("不得说“已直接渲染在上方”", skill)
        self.assertIn("内部交付标记而非虚假送达描述", skill)
        self.assertIn("patch_qwenpaw_local_artifact_delivery.py", dockerfile)
        self.assertIn("from qwenpaw.app.channels import renderer", dockerfile)
        self.assertIn("必须调用 `jarvis-chart`", skill)
        self.assertIn("没有图片生成工具和 matplotlib", skill)
        self.assertIn("timeout 60s python", chart_skill)
        self.assertIn("不得使用 matplotlib、ImageGen", chart_skill)
        self.assertIn("fonts-wqy-zenhei", dockerfile)
        self.assertIn("artifact_renderer_patch=", status_script)
        self.assertIn(
            "JARVIS_DINGTALK_MEDIA_RECEIPT_PATCH_V2", status_script
        )
        self.assertIn("chart_skill=", status_script)
        self.assertIn("chart_renderer=", status_script)
        self.assertIn("chart_font=", status_script)

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
