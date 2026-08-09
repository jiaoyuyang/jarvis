import re
from datetime import datetime



def _clean(text):
    return (text or "").strip()


def _limit(text, max_len):
    text = _clean(text)
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "\n\n📌 内容较长，已截断。可以继续追问。"


def format_answer_for_card(answer):
    """Turn Markdown structure into a scan-friendly form accepted by the fixed DingTalk card."""
    source = _clean(answer)
    output = []

    for line in source.splitlines():
        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(1).strip()
            prefix = "" if re.match(r"^[^\w\s]", title) else "🧭 "
            if output and output[-1]:
                output.append("")
            output.append(f"**{prefix}{title}**")
            continue

        if line.strip() in {"---", "***", "___"}:
            if output and output[-1]:
                output.append("")
            output.append("────────")
            continue

        quote = re.match(r"^\s*>\s*(.+?)\s*$", line)
        if quote:
            output.append(f"💡 {quote.group(1)}")
            continue

        output.append(line.rstrip())

    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


def _short_question(question, max_len=46):
    text = re.sub(r"\s+", " ", _clean(question))
    if not text or text in {"Jarvis 助手", "Codex入口"}:
        return ""
    return text if len(text) <= max_len else text[:max_len].rstrip() + "…"


def _card_tag(question, answer):
    text = f"{question or ''}\n{answer or ''}".lower()
    if any(word in text for word in ("报错", "异常", "失败", "排查", "错误", "不生效")):
        return "问题排查"
    if any(word in text for word in ("生成ppt", "ppt", "word", "文档", "文件")):
        return "文件处理"
    if any(word in text for word in ("图片", "截图", "图像", "界面")):
        return "图像分析"
    if any(word in text for word in ("代码", "脚本", "配置", "命令", "部署", "服务器")):
        return "执行任务"
    if any(word in text for word in ("为什么", "怎么", "如何", "是否", "分析", "解读", "什么意思", "比较")):
        return "洞察解读"
    return "智能回复"


def build_card_data(question, answer):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    tag = _card_tag(question, answer)
    question_summary = _short_question(question)
    answer = _limit(format_answer_for_card(answer), 3500)

    return {
        "title": "Jarvis 助手",
        "tag": tag,
        "question": f"本次问题 · {question_summary}" if question_summary else "",
        "answer": answer,
        "footer_brand": "Jarvis",
        "footer_time": now_str,
        "footer": f"Jarvis · {tag} · {now_str}",
    }
