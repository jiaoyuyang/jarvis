# Layout JSON for editable PPT

Use canvas_width=1600 and canvas_height=900.

Example:

{
  "canvas_width": 1600,
  "canvas_height": 900,
  "background": "#FFFFFF",
  "slides": [
    {
      "background": "#FFFFFF",
      "elements": [
        {
          "type": "text",
          "x": 300,
          "y": 20,
          "w": 1000,
          "h": 60,
          "text": "页面标题",
          "font_size": 28,
          "bold": true,
          "color": "#222222",
          "align": "center"
        },
        {
          "type": "round_rect",
          "x": 300,
          "y": 110,
          "w": 1000,
          "h": 90,
          "fill": "#FFFFFF",
          "line": "#F05A23",
          "text": "分层区域",
          "font_size": 16,
          "color": "#F05A23",
          "bold": true,
          "align": "left"
        },
        {
          "type": "card",
          "x": 430,
          "y": 125,
          "w": 230,
          "h": 60,
          "title": "API 网关",
          "body": "统一入口 | 鉴权 | 限流",
          "fill": "#FFFFFF",
          "line": "#00A651",
          "font_size": 11
        }
      ]
    }
  ]
}

Supported element types:
- text
- rect
- round_rect
- lane
- footer_bar
- card
- line
- image

Rules:
1. Prefer editable text, rect, round_rect, card, line.
2. Do not use the whole screenshot as a background.
3. Complex icons may be approximated using simple symbols or omitted.
4. Follow the brand palette explicitly provided by the user; otherwise use a neutral white-background style.
