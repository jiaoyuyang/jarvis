#!/usr/bin/env python3
"""Fetch recent daily weather and render one PNG with Pillow.

The command intentionally combines geocoding, weather retrieval and rendering
so the agent does not need a separate browser tool or handwritten chart JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from render_chart import ChartInputError, _safe_path, _validate, _working_root, render_chart


GEOCODING_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
CITY_RE = re.compile(r"^[\w\u3400-\u9fff .·'’()-]{1,80}$", re.UNICODE)
HTTP_TIMEOUT_SECONDS = 12
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class WeatherDataError(ValueError):
    """Raised when weather data cannot safely produce a chart."""


def _get_json(
    endpoint: str,
    params: dict[str, Any],
    *,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    url = endpoint + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "JarvisChart/1.0"})
    try:
        with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise WeatherDataError("天气数据服务暂时不可用") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise WeatherDataError("天气数据响应过大")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WeatherDataError("天气数据响应格式错误") from exc
    if not isinstance(payload, dict):
        raise WeatherDataError("天气数据响应格式错误")
    return payload


def _resolve_city(city: str, *, opener: Callable[..., Any]) -> dict[str, Any]:
    city = city.strip()
    if not CITY_RE.fullmatch(city):
        raise WeatherDataError("城市名格式不合法")
    payload = _get_json(
        GEOCODING_ENDPOINT,
        {"name": city, "count": 1, "language": "zh", "format": "json"},
        opener=opener,
    )
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise WeatherDataError(f"未找到城市：{city}")
    location = results[0]
    if not isinstance(location, dict):
        raise WeatherDataError("城市解析结果格式错误")
    try:
        latitude = float(location["latitude"])
        longitude = float(location["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherDataError("城市解析结果缺少坐标") from exc
    return {
        "name": str(location.get("name") or city).strip()[:40],
        "admin1": str(location.get("admin1") or "").strip()[:40],
        "country": str(location.get("country") or "").strip()[:40],
        "latitude": latitude,
        "longitude": longitude,
        "timezone": str(location.get("timezone") or "auto"),
    }


def _weather_spec(
    city: str,
    days: int,
    *,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    if not 2 <= days <= 14:
        raise WeatherDataError("天数必须在 2 到 14 之间")
    location = _resolve_city(city, opener=opener)
    payload = _get_json(
        FORECAST_ENDPOINT,
        {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min",
            "past_days": days - 1,
            "forecast_days": 1,
            "timezone": location["timezone"],
        },
        opener=opener,
    )
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise WeatherDataError("天气数据缺少日度结果")
    dates = daily.get("time")
    highs = daily.get("temperature_2m_max")
    lows = daily.get("temperature_2m_min")
    if not all(isinstance(values, list) for values in (dates, highs, lows)):
        raise WeatherDataError("天气数据字段不完整")
    if not (len(dates) == len(highs) == len(lows) and len(dates) >= days):
        raise WeatherDataError("天气数据天数不足")
    dates = dates[-days:]
    highs = highs[-days:]
    lows = lows[-days:]
    if any(value is None for value in highs + lows):
        raise WeatherDataError("天气温度数据存在空值")

    location_label = location["name"]
    if location["admin1"] and location["admin1"] != location_label:
        location_label += f"·{location['admin1']}"
    spec = {
        "title": f"{location_label}近{days}日天气趋势",
        "subtitle": "日最高温与最低温（今日为最新预测）",
        "x_labels": [str(value)[5:].replace("-", "/") for value in dates],
        "series": [
            {"name": "最高温", "values": highs, "color": "#F05A23"},
            {"name": "最低温", "values": lows, "color": "#7F7F7F"},
        ],
        "y_label": "温度（℃）",
        "footer": "数据来源：Open-Meteo；前期为历史预报，今日为最新预测",
    }
    return _validate(spec)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        root = _working_root()
        output = _safe_path(args.output, root, must_exist=False)
        spec = _weather_spec(args.city, args.days)
        render_chart(spec, output)
    except (WeatherDataError, ChartInputError, OSError) as exc:
        print(f"render_weather_chart: error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "ok", "output": output.as_uri(), "source": "Open-Meteo"},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
