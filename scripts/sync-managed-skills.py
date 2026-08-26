#!/usr/bin/env python3
"""Synchronize repository-managed Jarvis skills into a QwenPaw workspace."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MANAGED_PREFIX = "jarvis-"


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        digest.update(relative.encode("utf-8"))
        if path.is_symlink():
            digest.update(b"L")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_dir():
            digest.update(b"D")
        elif path.is_file():
            digest.update(b"F")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def discover_managed_skills(source_root: Path) -> list[Path]:
    skills = sorted(
        path
        for path in source_root.glob(f"{MANAGED_PREFIX}*")
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    if not skills:
        raise RuntimeError(f"no managed Jarvis skills found in {source_root}")
    return skills


def sync_skill_files(source_root: Path, workspace: Path) -> tuple[list[str], list[str]]:
    """Copy changed managed skills atomically while preserving unrelated skills."""
    target_root = workspace / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    backup_root = workspace / "backups" / "managed-skills" / datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    installed: list[str] = []
    changed: list[str] = []

    for source in discover_managed_skills(source_root):
        name = source.name
        destination = target_root / name
        installed.append(name)
        if destination.is_dir() and _tree_digest(source) == _tree_digest(destination):
            continue

        staging_parent = Path(
            tempfile.mkdtemp(prefix=f".{name}.sync-", dir=target_root)
        )
        staged = staging_parent / name
        try:
            shutil.copytree(source, staged, symlinks=True)
            if destination.exists() or destination.is_symlink():
                backup_root.mkdir(parents=True, exist_ok=True)
                destination.rename(backup_root / name)
            staged.rename(destination)
            changed.append(name)
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

    return installed, changed


def enable_skills(workspace: Path, skill_names: list[str]) -> None:
    from qwenpaw.agents.skill_system import (
        SkillService,
        read_skill_manifest,
        reconcile_workspace_manifest,
    )

    reconcile_workspace_manifest(workspace)
    service = SkillService(workspace)
    for skill_name in skill_names:
        result = service.enable_skill(skill_name)
        if not result.get("success"):
            raise RuntimeError(f"failed to enable {skill_name}: {result}")

    manifest = read_skill_manifest(workspace).get("skills", {})
    disabled = [
        name for name in skill_names if not manifest.get(name, {}).get("enabled", False)
    ]
    if disabled:
        raise RuntimeError(f"managed Jarvis skills are still disabled: {disabled}")


def main() -> None:
    working_dir = Path(os.environ.get("QWENPAW_WORKING_DIR", "/app/working"))
    agent_id = os.environ.get("JARVIS_AGENT_ID", "default")
    workspace = Path(
        os.environ.get(
            "JARVIS_SKILL_WORKSPACE",
            str(working_dir / "workspaces" / agent_id),
        )
    )
    source_root = Path(
        os.environ.get("JARVIS_MANAGED_SKILLS_SOURCE", "/opt/jarvis/skills")
    )
    if not workspace.is_dir():
        raise RuntimeError(f"QwenPaw workspace does not exist: {workspace}")

    installed, changed = sync_skill_files(source_root, workspace)
    enable_skills(workspace, installed)
    print("managed_skills_enabled=" + ",".join(installed), flush=True)
    print(
        "managed_skills_changed=" + (",".join(changed) if changed else "none"),
        flush=True,
    )


if __name__ == "__main__":
    main()
