---
name: jarvis-chart
description: "当用户要求生成趋势图、折线图、数据图表或直接发送图表图片时使用；通过内置 Pillow 脚本生成真实 PNG，再交给钉钉媒体通道发送。"
metadata:
  qwenpaw:
    emoji: "📈"
---

# Jarvis 确定性图表生成

本技能用于生成数据图表 PNG。当前运行环境没有图片生成工具，也没有 matplotlib；
不得尝试调用不存在的图片工具、安装绘图库或等待模型自行绘图。

## 固定流程

1. 获取并核验数据，数据不足时直接说明缺口，不编造数值。
2. 在 `/app/working` 内创建一个不含空格的 ASCII JSON 文件。
3. JSON 使用以下结构，`x_labels` 为 2—31 项，`series` 为 1—4 项：

```json
{
  "title": "乌鲁木齐近7日天气趋势",
  "subtitle": "最高温与最低温",
  "x_labels": ["8/20", "8/21", "8/22"],
  "series": [
    {"name": "最高温", "values": [28, 29, 27], "color": "#F05A23"},
    {"name": "最低温", "values": [17, 18, 16], "color": "#7F7F7F"}
  ],
  "y_label": "温度（℃）",
  "footer": "数据来源：已核验的天气数据"
}
```

4. 使用固定命令生成 PNG，命令执行上限 60 秒：

```bash
timeout 60s python /opt/jarvis/skills/jarvis-chart/scripts/render_chart.py \
  --input /app/working/chart_input.json \
  --output /app/working/chart_output.png
```

5. 只有命令返回 `status=ok` 且输出文件存在时，最终答复才放入：
   `[图表](file:///app/working/chart_output.png)`。
6. 命令失败或超时时，立即停止，不重试、不换工具、不安装依赖，直接说明“图表生成失败”及简短原因。

## 硬约束

- 只使用内置脚本；不得使用 matplotlib、ImageGen、Mermaid、HTML 或远程制图服务。
- 不得把数据网页链接冒充图表，也不得输出本地路径让用户自行打开。
- 一次请求最多生成一张图；需要多张图时先询问用户优先级。
- 图表生成阶段总耗时不得超过 60 秒。
