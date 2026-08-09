"""Centralized, environment-configurable Jarvis filesystem paths."""
from __future__ import annotations

import os
from pathlib import Path


def _path_from_env(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).expanduser().resolve()


PROJECT_ROOT = _path_from_env("JARVIS_HOME", Path(__file__).resolve().parents[1])
DATA_DIR = _path_from_env("JARVIS_DATA_DIR", PROJECT_ROOT / "data")
MEMORY_ROOT = _path_from_env("JARVIS_MEMORY_DIR", PROJECT_ROOT / "memory")
WORKSPACE_ROOT = _path_from_env("JARVIS_WORKSPACE", PROJECT_ROOT / "workspace")
UPLOADS_DIR = WORKSPACE_ROOT / "uploads"
OUTPUT_DIR = WORKSPACE_ROOT / "outputs"

