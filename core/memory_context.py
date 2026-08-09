"""Safely load a bounded personal knowledge-base context for a single request."""
from __future__ import annotations

from pathlib import Path

from core.memory_retriever import MEMORY_ROOT


MAX_CONTEXT_BYTES = 20 * 1024


class MemoryContext:
    def __init__(self, memory_root: str | Path = MEMORY_ROOT, max_bytes: int = MAX_CONTEXT_BYTES):
        self.memory_root = Path(memory_root).resolve()
        self.max_bytes = max_bytes

    def load(self, paths: list[Path]) -> str:
        """Read selected files up to the aggregate byte limit, with an explicit marker."""
        chunks: list[bytes] = []
        remaining = self.max_bytes
        truncated = False
        marker = "\n\n[个人知识库上下文已按 20KB 上限截断]".encode("utf-8")

        for path in paths:
            resolved = Path(path).resolve()
            if self.memory_root not in resolved.parents or not resolved.is_file():
                continue
            header = f"\n\n--- {resolved.relative_to(self.memory_root)} ---\n".encode("utf-8")
            content = resolved.read_bytes()
            block = header + content
            if len(block) <= remaining:
                chunks.append(block)
                remaining -= len(block)
                continue
            content_limit = max(remaining - len(marker), 0)
            if content_limit:
                chunks.append(block[:content_limit])
            truncated = True
            break

        if not chunks:
            return ""
        text = b"".join(chunks).decode("utf-8", errors="ignore").strip()
        if truncated:
            text += marker.decode("utf-8")
        return text
