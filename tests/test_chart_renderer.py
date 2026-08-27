import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills/jarvis-chart/scripts/render_chart.py"
WEATHER_SCRIPT = ROOT / "skills/jarvis-chart/scripts/render_weather_chart.py"
SKILL = ROOT / "skills/jarvis-chart/SKILL.md"


class ChartRendererTest(unittest.TestCase):
    def run_renderer(self, working: Path, input_path: Path, output_path: Path):
        env = os.environ.copy()
        env["QWENPAW_WORKING_DIR"] = str(working)
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )

    def test_generates_a_real_png_without_matplotlib(self) -> None:
        self.assertNotIn("matplotlib", SCRIPT.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            working = Path(temp_dir)
            input_path = working / "weather.json"
            output_path = working / "weather.png"
            input_path.write_text(
                json.dumps(
                    {
                        "title": "乌鲁木齐近7日天气趋势",
                        "subtitle": "最高温与最低温",
                        "x_labels": ["8/20", "8/21", "8/22", "8/23"],
                        "series": [
                            {"name": "最高温", "values": [28, 29, 27, 25]},
                            {"name": "最低温", "values": [17, 18, 16, 15]},
                        ],
                        "y_label": "温度（℃）",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = self.run_renderer(
                working,
                input_path,
                output_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"status": "ok"', result.stdout)
            self.assertTrue(output_path.is_file())
            with Image.open(output_path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (1200, 720))
                colors = image.convert("RGB").getcolors(maxcolors=1_000_000)
                self.assertIsNotNone(colors)
                self.assertGreater(len(colors or []), 8)

    def test_skill_uses_the_container_venv_interpreter(self) -> None:
        instructions = SKILL.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            instructions.count("timeout 60s /app/venv/bin/python"),
            2,
        )
        self.assertNotIn("timeout 60s python ", instructions)
        self.assertIn("render_weather_chart.py", instructions)
        self.assertTrue(WEATHER_SCRIPT.is_file())

    def test_rejects_paths_outside_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            working = base / "working"
            working.mkdir()
            input_path = working / "chart.json"
            input_path.write_text(
                json.dumps(
                    {
                        "x_labels": ["A", "B"],
                        "series": [{"name": "值", "values": [1, 2]}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = self.run_renderer(
                working,
                input_path,
                base / "outside.png",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("inside the working directory", result.stderr)

    def test_rejects_mismatched_series_without_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            working = Path(temp_dir)
            input_path = working / "bad.json"
            output_path = working / "bad.png"
            input_path.write_text(
                json.dumps(
                    {
                        "x_labels": ["A", "B", "C"],
                        "series": [{"name": "值", "values": [1, 2]}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = self.run_renderer(
                working,
                input_path,
                output_path,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("must match x_labels length", result.stderr)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
