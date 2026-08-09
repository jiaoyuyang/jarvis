"""Retrieve relevant private memory files without hard-coded business knowledge."""
from __future__ import annotations

import os
import re
from pathlib import Path

from core.paths import MEMORY_ROOT


class MemoryRetriever:
    """Small deterministic retriever over Markdown files in the private memory root."""

    DEFAULT_FILES = tuple(
        part.strip()
        for part in os.getenv("JARVIS_MEMORY_DEFAULT_FILES", "user/preferences.md").split(",")
        if part.strip()
    )
    MAX_SELECTED_FILES = max(1, min(int(os.getenv("JARVIS_MEMORY_MAX_FILES", "4")), 12))
    MAX_INDEX_FILES = max(1, min(int(os.getenv("JARVIS_MEMORY_INDEX_FILES", "64")), 256))
    MAX_INDEX_CHARS = max(1024, min(int(os.getenv("JARVIS_MEMORY_INDEX_CHARS", "32768")), 131072))

    def __init__(self, memory_root: str | Path = MEMORY_ROOT):
        self.memory_root = Path(memory_root).resolve()

    def retrieve(self, user_message: str | None) -> list[Path]:
        query_tokens = self._tokens(user_message or "")
        selected = self._resolve_unique(list(self.DEFAULT_FILES))
        selected_set = set(selected)
        if not query_tokens or not self.memory_root.is_dir():
            return selected

        ranked: list[tuple[int, str, Path]] = []
        for path in sorted(self.memory_root.rglob("*.md"))[: self.MAX_INDEX_FILES]:
            resolved = path.resolve()
            if resolved in selected_set or self.memory_root not in resolved.parents:
                continue
            try:
                content = resolved.read_text(encoding="utf-8", errors="ignore")[: self.MAX_INDEX_CHARS]
            except OSError:
                continue
            relative = str(resolved.relative_to(self.memory_root))
            path_tokens = self._tokens(relative.replace("_", " ").replace("/", " "))
            content_tokens = self._tokens(content)
            score = 4 * len(query_tokens & path_tokens) + len(query_tokens & content_tokens)
            if score:
                ranked.append((score, relative, resolved))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        for _score, _relative, path in ranked:
            if len(selected) >= self.MAX_SELECTED_FILES:
                break
            selected.append(path)
        return selected

    @staticmethod
    def _tokens(text: str) -> set[str]:
        text = str(text or "").lower()
        tokens = set(re.findall(r"[a-z][a-z0-9_-]{1,}|\d{2,}", text))
        cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text)
        for run in cjk_runs:
            if len(run) == 1:
                tokens.add(run)
                continue
            tokens.update(run[index:index + 2] for index in range(len(run) - 1))
            if len(run) >= 3:
                tokens.update(run[index:index + 3] for index in range(len(run) - 2))
        return tokens

    def _resolve_unique(self, relative_paths: list[str]) -> list[Path]:
        selected: list[Path] = []
        seen: set[Path] = set()
        for relative_path in relative_paths:
            path = (self.memory_root / relative_path).resolve()
            if self.memory_root not in path.parents or path in seen:
                continue
            seen.add(path)
            if path.is_file():
                selected.append(path)
        return selected
