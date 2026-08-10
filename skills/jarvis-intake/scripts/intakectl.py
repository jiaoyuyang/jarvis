#!/usr/bin/env python3
"""Register immutable Jarvis source materials from the agent workspace."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
KINDS = ("meeting", "document", "presentation", "spreadsheet", "image", "transcript", "other")
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
MAX_BYTES_DEFAULT = 100 * 1024 * 1024


def now() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def normalize(value: str, name: str, limit: int) -> str:
    value = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
    if not value:
        raise ValueError(f"{name}不能为空")
    if len(value) > limit:
        raise ValueError(f"{name}超过{limit}个字符")
    return value


def validate_project(value: str) -> str:
    value = normalize(value, "project", 64).lower()
    if not PROJECT_RE.fullmatch(value):
        raise ValueError("project只允许小写字母、数字和连字符")
    return value


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def append_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


@contextmanager
def locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".intake.lock"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    os.chmod(path, 0o600)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"材料账本第{number}行损坏") from exc
            if record.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(f"材料账本第{number}行版本不兼容")
            records.append(record)
    return records


def resolve_source(workspace: Path, source_value: str, max_bytes: int) -> Path:
    candidate = Path(source_value).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    if candidate.is_symlink():
        raise ValueError("source不能是符号链接")
    source = candidate.resolve(strict=True)
    allowed = False
    for dirname in ("media", "uploads"):
        root = (workspace / dirname).resolve()
        try:
            source.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise ValueError("只允许归档当前工作区 media/ 或 uploads/ 中的文件")
    if not source.is_file():
        raise ValueError("source必须是普通文件，不能是目录或符号链接")
    size = source.stat().st_size
    if size <= 0:
        raise ValueError("拒绝归档空文件")
    if size > max_bytes:
        raise ValueError(f"文件超过归档上限 {max_bytes} 字节")
    return source


def source_id(digest: str) -> str:
    stamp = now().strftime("%Y%m%d-%H%M%S")
    return f"src-{stamp}-{digest[:10]}-{uuid.uuid4().hex[:6]}"


def safe_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        return suffix
    return ".bin"


def copy_verified(source: Path, destination: Path, expected: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=False)
    temp = destination.parent / f".{destination.name}.partial"
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if sha256(temp) != expected:
            raise ValueError("归档副本校验失败")
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()


def register(args: argparse.Namespace, workspace: Path, intake_root: Path) -> None:
    max_bytes = args.max_bytes
    if max_bytes < 1 or max_bytes > MAX_BYTES_DEFAULT:
        raise ValueError(f"max-bytes必须在1到{MAX_BYTES_DEFAULT}之间")
    source = resolve_source(workspace, args.source, max_bytes)
    project = validate_project(args.project)
    title = normalize(args.title, "title", 200)
    label = normalize(args.source_label, "source-label", 500)
    digest = sha256(source)
    ledger = intake_root / "ledger.jsonl"
    records = load_ledger(ledger)
    for existing in records:
        if existing["sha256"] == digest:
            print(f"duplicate={existing['id']} project={existing['project']}")
            return

    created = now()
    material_id = source_id(digest)
    relative_dir = Path("knowledge/sources") / created.strftime("%Y/%m/%d") / material_id
    archive_dir = workspace / relative_dir
    archived_file = archive_dir / f"original{safe_suffix(source)}"
    copy_verified(source, archived_file, digest)

    record = {
        "schema_version": SCHEMA_VERSION,
        "id": material_id,
        "created_at": created.isoformat(timespec="seconds"),
        "project": project,
        "kind": args.kind,
        "title": title,
        "source_label": label,
        "original_name": source.name,
        "archive": str(archived_file.relative_to(workspace)),
        "sha256": digest,
        "size": source.stat().st_size,
    }
    atomic_write(
        archive_dir / "manifest.json",
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    append_line(ledger, record)
    print(f"registered={material_id} project={project} kind={args.kind}")


def verify(workspace: Path, intake_root: Path) -> None:
    records = load_ledger(intake_root / "ledger.jsonl")
    seen: set[str] = set()
    for record in records:
        if record["id"] in seen:
            raise ValueError(f"材料ID重复: {record['id']}")
        seen.add(record["id"])
        archive = (workspace / record["archive"]).resolve()
        allowed_root = (workspace / "knowledge/sources").resolve()
        try:
            archive.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError(f"归档路径越界: {record['id']}") from exc
        if not archive.is_file():
            raise ValueError(f"归档文件缺失: {record['id']}")
        if archive.stat().st_size != record["size"]:
            raise ValueError(f"归档大小不一致: {record['id']}")
        if sha256(archive) != record["sha256"]:
            raise ValueError(f"归档校验失败: {record['id']}")
    print(f"verified_materials={len(records)}")


def status(intake_root: Path) -> None:
    records = load_ledger(intake_root / "ledger.jsonl")
    total_bytes = sum(item["size"] for item in records)
    projects = len({item["project"] for item in records})
    print(f"materials={len(records)}")
    print(f"projects={projects}")
    print(f"bytes={total_bytes}")


def listing(args: argparse.Namespace, intake_root: Path) -> None:
    records = load_ledger(intake_root / "ledger.jsonl")
    if args.project:
        project = validate_project(args.project)
        records = [item for item in records if item["project"] == project]
    for item in reversed(records[-args.limit :]):
        print(f"{item['id']}\t{item['project']}\t{item['kind']}\t{item['title']}")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jarvis immutable material intake")
    parser.add_argument("--workspace", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--source", required=True)
    reg.add_argument("--project", required=True)
    reg.add_argument("--kind", choices=KINDS, required=True)
    reg.add_argument("--title", required=True)
    reg.add_argument("--source-label", required=True)
    reg.add_argument("--max-bytes", type=int, default=MAX_BYTES_DEFAULT)
    sub.add_parser("verify")
    sub.add_parser("status")
    ls = sub.add_parser("list")
    ls.add_argument("--project")
    ls.add_argument("--limit", type=int, default=20)
    return parser


def main() -> int:
    args = make_parser().parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir() or workspace == Path("/"):
        print(f"invalid workspace: {workspace}", file=sys.stderr)
        return 2
    intake_root = workspace / "knowledge/intake"
    try:
        with locked(intake_root):
            if args.command == "register":
                register(args, workspace, intake_root)
            elif args.command == "verify":
                verify(workspace, intake_root)
            elif args.command == "status":
                status(intake_root)
            elif args.command == "list":
                listing(args, intake_root)
    except (OSError, ValueError, KeyError) as exc:
        print(f"intakectl: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
