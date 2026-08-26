#!/usr/bin/env python3
"""Render a bounded line chart with Pillow only.

Input is a small JSON document under QWENPAW_WORKING_DIR.  Output must also be
inside that directory so the DingTalk artifact bridge can safely upload it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

from PIL import Image, ImageDraw, ImageFont


MAX_INPUT_BYTES = 1024 * 1024
MAX_POINTS = 31
MAX_SERIES = 4
DEFAULT_WIDTH = 1200
DEFAULT_HEIGHT = 720
MIN_WIDTH = 800
MAX_WIDTH = 1600
MIN_HEIGHT = 480
MAX_HEIGHT = 1000

PALETTE = ("#F05A23", "#7F7F7F", "#FEBE91", "#5B9BD5")
BACKGROUND = "#FFFFFF"
TEXT = "#333333"
MUTED = "#7F7F7F"
GRID = "#E8E8E8"


class ChartInputError(ValueError):
    """Raised when chart input is unsafe or invalid."""


def _working_root() -> Path:
    root = Path(os.environ.get("QWENPAW_WORKING_DIR", "/app/working"))
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise ChartInputError("working directory is unavailable") from exc


def _safe_path(raw: str, root: Path, *, must_exist: bool) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise ChartInputError(f"path is unavailable: {path.name}") from exc
    if resolved == root or root not in resolved.parents:
        raise ChartInputError("path must stay inside the working directory")
    return resolved


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
        if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ChartInputError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ChartInputError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ChartInputError(f"{label} must be finite")
    return result


def _validate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ChartInputError("input must be a JSON object")
    title = str(payload.get("title") or "趋势图").strip()[:80]
    subtitle = str(payload.get("subtitle") or "").strip()[:120]
    y_label = str(payload.get("y_label") or "").strip()[:24]
    footer = str(payload.get("footer") or "").strip()[:160]

    labels = payload.get("x_labels")
    if not isinstance(labels, list) or not 2 <= len(labels) <= MAX_POINTS:
        raise ChartInputError("x_labels must contain 2 to 31 labels")
    x_labels = [str(value).strip()[:20] for value in labels]
    if any(not value for value in x_labels):
        raise ChartInputError("x_labels cannot contain empty values")

    raw_series = payload.get("series")
    if not isinstance(raw_series, list) or not 1 <= len(raw_series) <= MAX_SERIES:
        raise ChartInputError("series must contain 1 to 4 entries")
    series = []
    for index, item in enumerate(raw_series):
        if not isinstance(item, dict):
            raise ChartInputError("each series must be an object")
        name = str(item.get("name") or f"系列 {index + 1}").strip()[:24]
        values = item.get("values")
        if not isinstance(values, list) or len(values) != len(x_labels):
            raise ChartInputError("each series must match x_labels length")
        color = str(item.get("color") or PALETTE[index]).strip()
        if not (
            len(color) == 7
            and color.startswith("#")
            and all(char in "0123456789abcdefABCDEF" for char in color[1:])
        ):
            color = PALETTE[index]
        series.append(
            {
                "name": name,
                "values": [
                    _number(value, f"series[{index}].values") for value in values
                ],
                "color": color,
            },
        )

    width = int(_number(payload.get("width", DEFAULT_WIDTH), "width"))
    height = int(_number(payload.get("height", DEFAULT_HEIGHT), "height"))
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise ChartInputError("width must be between 800 and 1600")
    if not MIN_HEIGHT <= height <= MAX_HEIGHT:
        raise ChartInputError("height must be between 480 and 1000")
    return {
        "title": title,
        "subtitle": subtitle,
        "y_label": y_label,
        "footer": footer,
        "x_labels": x_labels,
        "series": series,
        "width": width,
        "height": height,
    }


def _format_tick(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def render_chart(spec: dict[str, Any], output: Path) -> None:
    width = spec["width"]
    height = spec["height"]
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    title_font = _load_font(max(26, width // 36), bold=True)
    subtitle_font = _load_font(max(17, width // 64))
    axis_font = _load_font(max(15, width // 72))
    legend_font = _load_font(max(16, width // 68))
    footer_font = _load_font(max(14, width // 78))

    draw.text((70, 38), spec["title"], fill=TEXT, font=title_font)
    if spec["subtitle"]:
        draw.text((72, 87), spec["subtitle"], fill=MUTED, font=subtitle_font)

    left, right = 105, width - 60
    top = 155 if spec["subtitle"] else 130
    bottom = height - 115
    plot_width = right - left
    plot_height = bottom - top

    all_values = [
        value for item in spec["series"] for value in item["values"]
    ]
    minimum, maximum = min(all_values), max(all_values)
    if minimum == maximum:
        padding = max(abs(minimum) * 0.1, 1.0)
    else:
        padding = (maximum - minimum) * 0.12
    y_min = minimum - padding
    y_max = maximum + padding

    ticks = 5
    for index in range(ticks + 1):
        ratio = index / ticks
        y = bottom - ratio * plot_height
        value = y_min + ratio * (y_max - y_min)
        draw.line((left, y, right, y), fill=GRID, width=1)
        label = _format_tick(value)
        box = draw.textbbox((0, 0), label, font=axis_font)
        draw.text(
            (left - (box[2] - box[0]) - 14, y - (box[3] - box[1]) / 2),
            label,
            fill=MUTED,
            font=axis_font,
        )
    draw.line((left, top, left, bottom), fill="#B8B8B8", width=2)
    draw.line((left, bottom, right, bottom), fill="#B8B8B8", width=2)

    count = len(spec["x_labels"])
    x_positions = [
        left + (plot_width * index / (count - 1)) for index in range(count)
    ]
    for x, label in zip(x_positions, spec["x_labels"]):
        box = draw.textbbox((0, 0), label, font=axis_font)
        draw.text(
            (x - (box[2] - box[0]) / 2, bottom + 16),
            label,
            fill=MUTED,
            font=axis_font,
        )

    for item in spec["series"]:
        points = []
        for x, value in zip(x_positions, item["values"]):
            y = bottom - ((value - y_min) / (y_max - y_min)) * plot_height
            points.append((x, y))
        draw.line(points, fill=item["color"], width=4, joint="curve")
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=item["color"])

    legend_x = left
    legend_y = height - 60
    for item in spec["series"]:
        draw.line(
            (legend_x, legend_y + 9, legend_x + 30, legend_y + 9),
            fill=item["color"],
            width=5,
        )
        draw.text(
            (legend_x + 40, legend_y),
            item["name"],
            fill=TEXT,
            font=legend_font,
        )
        box = draw.textbbox((0, 0), item["name"], font=legend_font)
        legend_x += 65 + (box[2] - box[0])

    if spec["y_label"]:
        draw.text((left, top - 30), spec["y_label"], fill=MUTED, font=axis_font)
    if spec["footer"]:
        footer_box = draw.textbbox((0, 0), spec["footer"], font=footer_font)
        draw.text(
            (right - (footer_box[2] - footer_box[0]), height - 38),
            spec["footer"],
            fill=MUTED,
            font=footer_font,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.png")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        root = _working_root()
        input_path = _safe_path(args.input, root, must_exist=True)
        output_path = _safe_path(args.output, root, must_exist=False)
        if input_path.stat().st_size > MAX_INPUT_BYTES:
            raise ChartInputError("input JSON is too large")
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        spec = _validate(payload)
        render_chart(spec, output_path)
    except (ChartInputError, json.JSONDecodeError, OSError) as exc:
        print(f"render_chart: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "output": output_path.as_uri()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
