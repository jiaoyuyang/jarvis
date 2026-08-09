def render_card(question, answer):

    lines = answer.split("\n")

    return {
        "title": "🤖 Codex Agent 2.2",
        "question": question[:120],
        "summary": lines[0][:120] if lines else "完成",
        "body": "\n".join(lines[1:])[:1500],
        "footer": "stable · pool · timeout-safe"
    }
