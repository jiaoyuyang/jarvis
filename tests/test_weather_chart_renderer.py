import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills/jarvis-chart/scripts/render_weather_chart.py"
RENDERER_DIR = SCRIPT.parent


def load_weather_module():
    import sys

    sys.path.insert(0, str(RENDERER_DIR))
    try:
        spec = importlib.util.spec_from_file_location("weather_renderer", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class WeatherChartRendererTest(unittest.TestCase):
    def setUp(self):
        self.module = load_weather_module()

    def fake_opener(self, request, timeout):
        self.assertEqual(timeout, 12)
        if "geocoding-api" in request.full_url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "name": "乌鲁木齐",
                            "admin1": "新疆",
                            "country": "中国",
                            "latitude": 43.8256,
                            "longitude": 87.6168,
                            "timezone": "Asia/Urumqi",
                        }
                    ]
                }
            )
        return FakeResponse(
            {
                "daily": {
                    "time": [
                        "2026-08-21",
                        "2026-08-22",
                        "2026-08-23",
                        "2026-08-24",
                        "2026-08-25",
                        "2026-08-26",
                        "2026-08-27",
                    ],
                    "temperature_2m_max": [28, 29, 30, 27, 26, 25, 24],
                    "temperature_2m_min": [17, 18, 19, 16, 15, 14, 13],
                }
            }
        )

    def test_fetches_and_renders_weather_png(self):
        spec = self.module._weather_spec("乌鲁木齐", 7, opener=self.fake_opener)
        self.assertEqual(spec["x_labels"][0], "08/21")
        self.assertEqual(spec["series"][0]["values"][-1], 24.0)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "weather.png"
            self.module.render_chart(spec, output)
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (1200, 720))

    def test_rejects_missing_or_partial_weather_data(self):
        def partial_opener(request, timeout):
            if "geocoding-api" in request.full_url:
                return self.fake_opener(request, timeout)
            return FakeResponse(
                {
                    "daily": {
                        "time": ["2026-08-27"],
                        "temperature_2m_max": [24],
                        "temperature_2m_min": [13],
                    }
                }
            )

        with self.assertRaisesRegex(self.module.WeatherDataError, "天数不足"):
            self.module._weather_spec("乌鲁木齐", 7, opener=partial_opener)

    def test_rejects_unsafe_city_and_day_count(self):
        with self.assertRaisesRegex(self.module.WeatherDataError, "城市名格式"):
            self.module._weather_spec("x'; rm -rf /", 7, opener=self.fake_opener)
        with self.assertRaisesRegex(self.module.WeatherDataError, "2 到 14"):
            self.module._weather_spec("乌鲁木齐", 31, opener=self.fake_opener)


if __name__ == "__main__":
    unittest.main()
