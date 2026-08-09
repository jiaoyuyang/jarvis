import re


def clean_markdown(text):
    text = (text or "").strip()

    # 去掉 plain text 灰框
    text = re.sub(
        r"```(?:plain text|plaintext|text)\n([\s\S]*?)\n```",
        lambda m: m.group(1).strip(),
        text,
        flags=re.IGNORECASE,
    )

    # 去掉常见寒暄
    prefixes = [
        "你好，",
        "好的，",
        "可以，",
    ]
    for p in prefixes:
        if text.startswith(p):
            text = text[len(p):].lstrip()

    # 控制空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def compact_for_dingtalk(text, max_chars=1800):
    text = clean_markdown(text)

    # 太长就截断，避免钉钉变成“大白纸”
    if len(text) <= max_chars:
        return text

    cut = text.rfind("\n\n", 0, max_chars)
    if cut < 600:
        cut = text.rfind("\n", 0, max_chars)
    if cut < 600:
        cut = max_chars

    return text[:cut].strip() + "\n\n📌 内容较长，我先收住。需要的话你可以继续问：展开方案 / 给案例 / 做成PPT。"


def split_markdown(text, max_len=1800):
    text = compact_for_dingtalk(text, max_len)

    if len(text) <= max_len:
        return [text]

    chunks = []
    while len(text) > max_len:
        cut = text.rfind("\n\n", 0, max_len)
        if cut < 600:
            cut = text.rfind("\n", 0, max_len)
        if cut < 600:
            cut = max_len

        chunks.append(text[:cut].strip())
        text = text[cut:].strip()

    if text:
        chunks.append(text)

    return chunks
