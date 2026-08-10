#!/usr/bin/env python3
"""Jarvis two-tier memory ledger.

The append-only ledger is the source of truth. Human-readable inbox and curated
Markdown files are projections used by Codex retrieval. No command physically
deletes a memory; corrections supersede old records and retirement creates a
tombstone.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
MEMORY_TYPES = ("fact", "preference", "decision", "standard", "todo")
CATEGORIES = (
    "profile",
    "people",
    "preferences",
    "projects",
    "decisions",
    "standards",
    "other",
)
STATUSES = ("pending", "confirmed", "active", "superseded", "retired")
DEFAULT_CATEGORY = {
    "fact": "profile",
    "preference": "preferences",
    "decision": "decisions",
    "standard": "standards",
    "todo": "projects",
}
CATEGORY_TITLES = {
    "profile": "用户资料",
    "people": "人物",
    "preferences": "偏好",
    "projects": "项目",
    "decisions": "决策",
    "standards": "规则与标准",
    "other": "其他",
}
SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"(?:password|passwd|api[_ -]?key|access[_ -]?token|secret|密码|口令)"
        r"\s*[:=：]\s*\S{4,}",
        re.I,
    ),
    re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_text(value: str, *, field: str, max_length: int) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = " ".join(value.split()).strip()
    if not value:
        raise ValueError(f"{field}不能为空")
    if len(value) > max_length:
        raise ValueError(f"{field}超过{max_length}个字符")
    return value


def normalize_key(value: str) -> str:
    value = normalize_text(value, field="key", max_length=120).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value):
        raise ValueError("key只允许小写字母、数字、点、下划线和连字符")
    return value


def reject_sensitive(*values: str) -> None:
    combined = "\n".join(values)
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(combined):
            raise ValueError("检测到疑似密码、Token、私钥或个人敏感号码，拒绝写入记忆")


def memory_id(content: str) -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    entropy = hashlib.sha256(
        f"{content}\0{uuid.uuid4().hex}".encode("utf-8")
    ).hexdigest()[:8]
    return f"mem-{stamp}-{entropy}"


def event_id() -> str:
    return f"evt-{uuid.uuid4().hex}"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def append_fsync(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


@contextmanager
def locked(memory_root: Path) -> Iterator[None]:
    memory_root.mkdir(parents=True, exist_ok=True)
    lock_path = memory_root / ".memory.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    os.chmod(lock_path, 0o600)


def read_ledger(ledger_path: Path) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        return []
    events: list[dict[str, Any]] = []
    with ledger_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"账本第{line_number}行损坏: {exc}") from exc
            if event.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(f"账本第{line_number}行版本不受支持")
            events.append(event)
    return events


def build_state(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for event in events:
        for change in event.get("changes", []):
            target = change["id"]
            if target not in records:
                raise ValueError(f"账本引用不存在的记忆: {target}")
            records[target].update(change["set"])
        record = event.get("record")
        if record:
            record_id = record["id"]
            if record_id in records:
                raise ValueError(f"账本包含重复记忆ID: {record_id}")
            records[record_id] = dict(record)
    return records


def state_document(records: dict[str, dict[str, Any]]) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now_iso(),
        "memories": records,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_record(record: dict[str, Any]) -> str:
    lines = [
        f"## {record['key']}",
        "",
        f"- ID：`{record['id']}`",
        f"- 类型：{record['type']}",
        f"- 内容：{record['content']}",
        f"- 来源：{record['source']}",
        f"- 生效时间：{record['updated_at']}",
    ]
    if record.get("supersedes"):
        lines.append(f"- 取代：`{record['supersedes']}`")
    lines.append("")
    return "\n".join(lines)


def write_projections(memory_root: Path, records: dict[str, dict[str, Any]]) -> None:
    atomic_write(memory_root / "state.json", state_document(records))
    curated_root = memory_root / "curated"
    curated_root.mkdir(parents=True, exist_ok=True)
    active = [record for record in records.values() if record["status"] == "active"]
    active.sort(key=lambda record: (record["category"], record["key"], record["id"]))

    counts = Counter(record["category"] for record in active)
    index_lines = [
        "# Jarvis 当前长期记忆",
        "",
        "> 本目录由 memoryctl 根据追加式账本生成，请勿手工修改。",
        "",
        f"当前有效记忆：{len(active)} 条。",
        "",
    ]
    for category in CATEGORIES:
        index_lines.append(f"- {CATEGORY_TITLES[category]}：{counts[category]} 条")
    index_lines.append("")
    atomic_write(curated_root / "INDEX.md", "\n".join(index_lines))

    for category in CATEGORIES:
        title = CATEGORY_TITLES[category]
        lines = [
            f"# Jarvis 长期记忆：{title}",
            "",
            "> 自动生成的当前有效视图；历史版本保存在 `memory/ledger.jsonl`。",
            "",
        ]
        category_records = [r for r in active if r["category"] == category]
        if not category_records:
            lines.extend(["暂无记录。", ""])
        else:
            for record in category_records:
                lines.append(render_record(record))
        atomic_write(curated_root / f"{category}.md", "\n".join(lines))


def append_inbox(memory_root: Path, record: dict[str, Any]) -> None:
    date = record["created_at"][:10]
    path = memory_root / "inbox" / f"{date}.md"
    if not path.exists():
        append_fsync(path, f"# {date} 记忆收件箱\n\n")
    block = [
        f"## {record['id']}",
        "",
        f"- 时间：{record['created_at']}",
        f"- 类型：{record['type']}",
        f"- 记忆键：`{record['key']}`",
        f"- 初始状态：{record['status']}",
        f"- 内容：{record['content']}",
        f"- 来源：{record['source']}",
    ]
    if record.get("category"):
        block.append(f"- 分类：{record['category']}")
    if record.get("supersedes"):
        block.append(f"- 取代：`{record['supersedes']}`")
    if record.get("reason"):
        block.append(f"- 原因：{record['reason']}")
    block.extend(["", ""])
    append_fsync(path, "\n".join(block))


def make_record(args: argparse.Namespace, *, status: str) -> dict[str, Any]:
    content = normalize_text(args.content, field="content", max_length=2000)
    source = normalize_text(args.source, field="source", max_length=500)
    key = normalize_key(args.key)
    reject_sensitive(content, source)
    category = getattr(args, "category", None) or DEFAULT_CATEGORY[args.type]
    created = now_iso()
    return {
        "id": memory_id(content),
        "created_at": created,
        "updated_at": created,
        "type": args.type,
        "category": category,
        "key": key,
        "content": content,
        "source": source,
        "status": status,
        "supersedes": getattr(args, "supersedes", None),
        "reason": normalize_text(args.reason, field="reason", max_length=500)
        if getattr(args, "reason", None)
        else None,
    }


def active_for_key(
    records: dict[str, dict[str, Any]], key: str
) -> list[dict[str, Any]]:
    return [
        record
        for record in records.values()
        if record["key"] == key and record["status"] == "active"
    ]


def duplicate_record(
    records: dict[str, dict[str, Any]], record: dict[str, Any]
) -> dict[str, Any] | None:
    normalized = record["content"].casefold()
    for existing in records.values():
        if existing["status"] == "retired":
            continue
        if (
            existing["key"] == record["key"]
            and existing["content"].casefold() == normalized
        ):
            return existing
    return None


def validate_supersedes(
    records: dict[str, dict[str, Any]], record: dict[str, Any]
) -> list[dict[str, Any]]:
    active = active_for_key(records, record["key"])
    supersedes = record.get("supersedes")
    if active and not supersedes:
        ids = ", ".join(item["id"] for item in active)
        raise ValueError(
            f"记忆键 {record['key']} 已有有效版本 {ids}；"
            "请使用 correct，或明确指定 --supersedes"
        )
    if supersedes:
        old = records.get(supersedes)
        if not old:
            raise ValueError(f"要取代的记忆不存在: {supersedes}")
        if old["status"] != "active":
            raise ValueError(f"只能取代有效记忆，当前状态: {old['status']}")
        if old["key"] != record["key"]:
            raise ValueError("新旧记忆的 key 必须一致")
        return [
            {
                "id": supersedes,
                "set": {
                    "status": "superseded",
                    "updated_at": record["updated_at"],
                    "superseded_by": record["id"],
                    "supersede_reason": record.get("reason") or "用户确认了新版本",
                },
            }
        ]
    return []


def append_event(
    ledger: Path,
    *,
    action: str,
    record: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
) -> None:
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id(),
        "at": now_iso(),
        "action": action,
        "actor": "jarvis",
        "record": record,
        "changes": changes or [],
    }
    append_fsync(ledger, json.dumps(event, ensure_ascii=False) + "\n")


def refresh(memory_root: Path) -> dict[str, dict[str, Any]]:
    records = build_state(read_ledger(memory_root / "ledger.jsonl"))
    write_projections(memory_root, records)
    return records


def command_capture(args: argparse.Namespace, memory_root: Path) -> None:
    status = "confirmed" if args.confirmed else "pending"
    record = make_record(args, status=status)
    ledger = memory_root / "ledger.jsonl"
    records = build_state(read_ledger(ledger))
    duplicate = duplicate_record(records, record)
    if duplicate:
        print(f"duplicate={duplicate['id']}")
        return
    append_event(ledger, action="capture", record=record)
    append_inbox(memory_root, record)
    refresh(memory_root)
    print(f"captured={record['id']} status={status}")


def command_remember(args: argparse.Namespace, memory_root: Path) -> None:
    record = make_record(args, status="active")
    ledger = memory_root / "ledger.jsonl"
    records = build_state(read_ledger(ledger))
    duplicate = duplicate_record(records, record)
    if duplicate and duplicate["status"] == "active":
        print(f"duplicate={duplicate['id']} status=active")
        return
    changes = validate_supersedes(records, record)
    append_event(ledger, action="remember", record=record, changes=changes)
    append_inbox(memory_root, record)
    refresh(memory_root)
    print(f"remembered={record['id']} category={record['category']}")


def command_promote(args: argparse.Namespace, memory_root: Path) -> None:
    ledger = memory_root / "ledger.jsonl"
    records = build_state(read_ledger(ledger))
    record = records.get(args.id)
    if not record:
        raise ValueError(f"记忆不存在: {args.id}")
    if record["status"] == "active":
        print(f"already_active={args.id}")
        return
    if record["status"] not in ("pending", "confirmed"):
        raise ValueError(f"当前状态不能晋升: {record['status']}")
    category = args.category or record["category"]
    active = active_for_key(records, record["key"])
    if active:
        ids = ", ".join(item["id"] for item in active)
        raise ValueError(f"同一 key 已有有效记忆 {ids}，请使用 correct")
    append_event(
        ledger,
        action="promote",
        changes=[
            {
                "id": args.id,
                "set": {
                    "status": "active",
                    "category": category,
                    "updated_at": now_iso(),
                },
            }
        ],
    )
    refresh(memory_root)
    print(f"promoted={args.id} category={category}")


def command_correct(args: argparse.Namespace, memory_root: Path) -> None:
    ledger = memory_root / "ledger.jsonl"
    records = build_state(read_ledger(ledger))
    old = records.get(args.id)
    if not old:
        raise ValueError(f"记忆不存在: {args.id}")
    if old["status"] != "active":
        raise ValueError(f"只能更正有效记忆，当前状态: {old['status']}")
    args.key = old["key"]
    args.type = args.type or old["type"]
    args.category = args.category or old["category"]
    args.supersedes = old["id"]
    record = make_record(args, status="active")
    changes = validate_supersedes(records, record)
    append_event(ledger, action="correct", record=record, changes=changes)
    append_inbox(memory_root, record)
    refresh(memory_root)
    print(f"corrected={old['id']} replacement={record['id']}")


def command_retire(args: argparse.Namespace, memory_root: Path) -> None:
    reason = normalize_text(args.reason, field="reason", max_length=500)
    reject_sensitive(reason)
    ledger = memory_root / "ledger.jsonl"
    records = build_state(read_ledger(ledger))
    record = records.get(args.id)
    if not record:
        raise ValueError(f"记忆不存在: {args.id}")
    if record["status"] == "retired":
        print(f"already_retired={args.id}")
        return
    changed_at = now_iso()
    append_event(
        ledger,
        action="retire",
        changes=[
            {
                "id": args.id,
                "set": {
                    "status": "retired",
                    "updated_at": changed_at,
                    "retire_reason": reason,
                },
            }
        ],
    )
    refresh(memory_root)
    print(f"retired={args.id}")


def command_status(args: argparse.Namespace, memory_root: Path) -> None:
    records = refresh(memory_root)
    counts = Counter(record["status"] for record in records.values())
    print(f"total={len(records)}")
    for status in STATUSES:
        print(f"{status}={counts[status]}")
    print(f"ledger={memory_root / 'ledger.jsonl'}")
    print(f"curated={memory_root / 'curated'}")


def command_list(args: argparse.Namespace, memory_root: Path) -> None:
    records = refresh(memory_root)
    selected = [
        record
        for record in records.values()
        if (not args.status or record["status"] == args.status)
        and (not args.category or record["category"] == args.category)
    ]
    selected.sort(key=lambda record: (record["updated_at"], record["id"]), reverse=True)
    for record in selected[: args.limit]:
        print(
            f"{record['id']}\t{record['status']}\t{record['category']}\t"
            f"{record['key']}\t{record['content']}"
        )


def command_verify(args: argparse.Namespace, memory_root: Path) -> None:
    events = read_ledger(memory_root / "ledger.jsonl")
    records = build_state(events)
    for record in records.values():
        if record["type"] not in MEMORY_TYPES:
            raise ValueError(f"非法类型: {record['id']}")
        if record["category"] not in CATEGORIES:
            raise ValueError(f"非法分类: {record['id']}")
        if record["status"] not in STATUSES:
            raise ValueError(f"非法状态: {record['id']}")
        reject_sensitive(record["content"], record["source"])
    if args.rebuild:
        write_projections(memory_root, records)
    print(f"verified_events={len(events)} memories={len(records)}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Jarvis two-tier memory manager")
    root.add_argument("--workspace", default=".", help="Jarvis agent workspace")
    sub = root.add_subparsers(dest="command", required=True)

    def common(target: argparse.ArgumentParser, *, include_category: bool = True) -> None:
        target.add_argument("--type", choices=MEMORY_TYPES, required=True)
        if include_category:
            target.add_argument("--category", choices=CATEGORIES)
        target.add_argument("--key", required=True)
        target.add_argument("--content", required=True)
        target.add_argument("--source", required=True)

    capture = sub.add_parser("capture", help="write a candidate to the daily inbox")
    common(capture)
    capture.add_argument("--confirmed", action="store_true")
    capture.set_defaults(func=command_capture)

    remember = sub.add_parser("remember", help="write confirmed long-term memory")
    common(remember)
    remember.add_argument("--supersedes")
    remember.add_argument("--reason")
    remember.set_defaults(func=command_remember)

    promote = sub.add_parser("promote", help="promote an inbox candidate")
    promote.add_argument("id")
    promote.add_argument("--category", choices=CATEGORIES)
    promote.set_defaults(func=command_promote)

    correct = sub.add_parser("correct", help="replace an active memory version")
    correct.add_argument("id")
    correct.add_argument("--type", choices=MEMORY_TYPES)
    correct.add_argument("--category", choices=CATEGORIES)
    correct.add_argument("--content", required=True)
    correct.add_argument("--source", required=True)
    correct.add_argument("--reason", required=True)
    correct.set_defaults(func=command_correct)

    retire = sub.add_parser("retire", help="logically retire a memory")
    retire.add_argument("id")
    retire.add_argument("--reason", required=True)
    retire.set_defaults(func=command_retire)

    status = sub.add_parser("status", help="show memory counts")
    status.set_defaults(func=command_status)

    listing = sub.add_parser("list", help="list memory records")
    listing.add_argument("--status", choices=STATUSES)
    listing.add_argument("--category", choices=CATEGORIES)
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(func=command_list)

    verify = sub.add_parser("verify", help="validate the append-only ledger")
    verify.add_argument("--rebuild", action="store_true")
    verify.set_defaults(func=command_verify)
    return root


def main() -> int:
    args = parser().parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir() or workspace == Path("/"):
        print(f"invalid workspace: {workspace}", file=sys.stderr)
        return 2
    memory_root = workspace / "memory"
    try:
        with locked(memory_root):
            args.func(args, memory_root)
    except (OSError, ValueError, KeyError) as exc:
        print(f"memoryctl: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
