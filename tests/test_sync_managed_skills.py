import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "sync-managed-skills.py"
SPEC = importlib.util.spec_from_file_location("sync_managed_skills", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def make_skill(root: Path, name: str, marker: str) -> None:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"# {name}\n{marker}\n", encoding="utf-8")


class SyncManagedSkillsTest(unittest.TestCase):
    def test_sync_replaces_managed_skills_and_preserves_user_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            workspace = root / "workspace"
            make_skill(source, "jarvis-memory", "new")
            make_skill(source, "jarvis-chart", "chart")
            make_skill(workspace / "skills", "jarvis-memory", "old")
            make_skill(workspace / "skills", "personal-skill", "keep")

            installed, changed = MODULE.sync_skill_files(source, workspace)

            self.assertEqual(installed, ["jarvis-chart", "jarvis-memory"])
            self.assertEqual(changed, ["jarvis-chart", "jarvis-memory"])
            self.assertIn(
                "new",
                (workspace / "skills/jarvis-memory/SKILL.md").read_text(),
            )
            self.assertTrue((workspace / "skills/jarvis-chart/SKILL.md").is_file())
            self.assertTrue((workspace / "skills/personal-skill/SKILL.md").is_file())

            _, changed_again = MODULE.sync_skill_files(source, workspace)
            self.assertEqual(changed_again, [])

    def test_enable_skills_reconciles_and_verifies_manifest(self):
        calls = []
        manifest = {"skills": {}}

        class FakeService:
            def __init__(self, workspace):
                calls.append(("service", workspace))

            def enable_skill(self, name):
                calls.append(("enable", name))
                manifest["skills"][name] = {"enabled": True}
                return {"success": True}

        skill_system = types.ModuleType("qwenpaw.agents.skill_system")
        skill_system.SkillService = FakeService
        skill_system.read_skill_manifest = lambda workspace: manifest
        skill_system.reconcile_workspace_manifest = lambda workspace: calls.append(
            ("reconcile", workspace)
        )
        fake_modules = {
            "qwenpaw": types.ModuleType("qwenpaw"),
            "qwenpaw.agents": types.ModuleType("qwenpaw.agents"),
            "qwenpaw.agents.skill_system": skill_system,
        }
        original = {name: sys.modules.get(name) for name in fake_modules}
        try:
            sys.modules.update(fake_modules)
            workspace = Path("/tmp/workspace")
            MODULE.enable_skills(workspace, ["jarvis-chart"])
        finally:
            for name, previous in original.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

        self.assertEqual(
            calls,
            [
                ("reconcile", Path("/tmp/workspace")),
                ("service", Path("/tmp/workspace")),
                ("enable", "jarvis-chart"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
