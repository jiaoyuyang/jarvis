#!/usr/bin/env python3
"""Append-only project state for Jarvis workspaces."""

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
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
KINDS = ("decision", "action", "milestone", "risk", "update")
STATUSES = ("open", "planned", "active", "blocked", "done", "cancelled", "noted")
DEFAULT_STATUS = {
    "decision": "noted",
    "action": "open",
    "milestone": "planned",
    "risk": "open",
    "update": "noted",
}
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def now() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def normalize(value: str, field: str, limit: int, *, required: bool = True) -> str:
    value = " ".join(unicodedata.normalize("NFKC", value or "").split()).strip()
    if required and not value:
        raise ValueError(f"{field}不能为空")
    if len(value) > limit:
        raise ValueError(f"{field}超过{limit}个字符")
    return value


def project_key(value: str) -> str:
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


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


@contextmanager
def locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".project.lock"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    os.chmod(path, 0o600)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON损坏: {path.name}") from exc


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"项目账本第{number}行损坏") from exc
            if event.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(f"项目账本第{number}行版本不兼容")
            events.append(event)
    return events


def build_state(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["action"] == "record":
            item = dict(event["item"])
            if item["id"] in items:
                raise ValueError(f"项目条目ID重复: {item['id']}")
            items[item["id"]] = item
        elif event["action"] == "change":
            target = event["target"]
            if target not in items:
                raise ValueError(f"项目变更引用不存在条目: {target}")
            items[target].update(event["set"])
        else:
            raise ValueError(f"未知项目事件: {event['action']}")
    return items


def item_id(kind: str, content: str) -> str:
    stamp = now().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(f"{kind}\0{content}\0{uuid.uuid4().hex}".encode()).hexdigest()[:8]
    return f"item-{stamp}-{digest}"


def event_id() -> str:
    return f"evt-{uuid.uuid4().hex}"


def project_dir(workspace: Path, key: str) -> Path:
    return workspace / "knowledge/projects" / key


def load_meta(root: Path) -> dict[str, Any]:
    meta = load_json(root / "meta.json")
    if not meta:
        raise ValueError("项目尚未初始化")
    if meta.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("项目元数据版本不兼容")
    return meta


def init_project(args: argparse.Namespace, workspace: Path) -> None:
    key = project_key(args.project)
    name = normalize(args.name, "name", 100)
    root = project_dir(workspace, key)
    meta_path = root / "meta.json"
    if meta_path.exists():
        meta = load_meta(root)
        if meta["name"] != name:
            raise ValueError(f"项目已存在且名称为: {meta['name']}")
        render(root, meta, build_state(load_events(root / "ledger.jsonl")))
        print(f"existing_project={key}")
        return
    root.mkdir(parents=True, exist_ok=False)
    meta = {
        "schema_version": SCHEMA_VERSION,
        "project": key,
        "name": name,
        "created_at": now_iso(),
    }
    atomic_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    render(root, meta, {})
    print(f"initialized={key}")


def ensure_due(value: str) -> str:
    value = normalize(value, "due", 10, required=False)
    if value and not DATE_RE.fullmatch(value):
        raise ValueError("due必须为YYYY-MM-DD")
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("due必须为有效的YYYY-MM-DD日期") from exc
    return value


def record_item(args: argparse.Namespace, workspace: Path) -> None:
    key = project_key(args.project)
    root = project_dir(workspace, key)
    meta = load_meta(root)
    events = load_events(root / "ledger.jsonl")
    items = build_state(events)
    title = normalize(args.title, "title", 160)
    content = normalize(args.content, "content", 2000)
    source = normalize(args.source, "source", 500)
    owner = normalize(args.owner, "owner", 100, required=False)
    due = ensure_due(args.due)
    for existing in items.values():
        if (
            existing["kind"] == args.kind
            and existing["title"].casefold() == title.casefold()
            and existing["content"].casefold() == content.casefold()
            and existing["source"].casefold() == source.casefold()
        ):
            print(f"duplicate={existing['id']}")
            return
    created = now_iso()
    item = {
        "id": item_id(args.kind, content),
        "kind": args.kind,
        "title": title,
        "content": content,
        "source": source,
        "owner": owner,
        "due": due,
        "status": args.status or DEFAULT_STATUS[args.kind],
        "created_at": created,
        "updated_at": created,
    }
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id(),
        "at": created,
        "action": "record",
        "item": item,
    }
    append_event(root / "ledger.jsonl", event)
    items[item["id"]] = item
    render(root, meta, items)
    print(f"recorded={item['id']} kind={args.kind}")


def change_item(args: argparse.Namespace, workspace: Path) -> None:
    projects_root = workspace / "knowledge/projects"
    matches: list[tuple[Path, dict[str, Any], dict[str, dict[str, Any]]]] = []
    if projects_root.is_dir():
        for root in projects_root.iterdir():
            if not root.is_dir() or not (root / "meta.json").exists():
                continue
            meta = load_meta(root)
            items = build_state(load_events(root / "ledger.jsonl"))
            if args.id in items:
                matches.append((root, meta, items))
    if len(matches) != 1:
        raise ValueError("找不到唯一的项目条目")
    root, meta, items = matches[0]
    note = normalize(args.note, "note", 500)
    changed = now_iso()
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id(),
        "at": changed,
        "action": "change",
        "target": args.id,
        "set": {"status": args.status, "updated_at": changed, "change_note": note},
    }
    append_event(root / "ledger.jsonl", event)
    items[args.id].update(event["set"])
    render(root, meta, items)
    print(f"changed={args.id} status={args.status}")


def format_item(item: dict[str, Any], *, include_status: bool = True) -> str:
    lines = [f"### {item['title']}", ""]
    if include_status:
        lines.append(f"- 状态：{item['status']}")
    if item.get("owner"):
        lines.append(f"- 责任人：{item['owner']}")
    if item.get("due"):
        lines.append(f"- 时间：{item['due']}")
    lines.extend([f"- 内容：{item['content']}", f"- 来源：{item['source']}", ""])
    return "\n".join(lines)


def render(root: Path, meta: dict[str, Any], items: dict[str, dict[str, Any]]) -> None:
    ordered = sorted(items.values(), key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    active_actions = [i for i in ordered if i["kind"] == "action" and i["status"] not in ("done", "cancelled")]
    active_risks = [i for i in ordered if i["kind"] == "risk" and i["status"] not in ("done", "cancelled")]
    milestones = [i for i in ordered if i["kind"] == "milestone"]
    updates = [i for i in ordered if i["kind"] == "update"]

    status_lines = [
        f"# {meta['name']}：当前状态",
        "",
        "> 由 projectctl 根据追加式项目账本生成，请勿手工修改。",
        "",
        f"- 待办：{len(active_actions)}",
        f"- 活跃风险：{len(active_risks)}",
        f"- 里程碑：{len(milestones)}",
        "",
        "## 最近进展",
        "",
    ]
    if updates:
        for item in updates[:10]:
            status_lines.extend([f"- {item['title']}：{item['content']}"])
    else:
        status_lines.append("暂无记录。")
    status_lines.extend(["", "## 当前风险", ""])
    if active_risks:
        for item in active_risks:
            status_lines.extend([f"- {item['title']}：{item['content']}"])
    else:
        status_lines.append("暂无记录。")
    status_lines.append("")
    atomic_write(root / "STATUS.md", "\n".join(status_lines))

    decision_lines = [f"# {meta['name']}：已确认决策", ""]
    decisions = [i for i in ordered if i["kind"] == "decision"]
    if decisions:
        decision_lines.extend(format_item(i, include_status=False) for i in decisions)
    else:
        decision_lines.extend(["暂无记录。", ""])
    atomic_write(root / "DECISIONS.md", "\n".join(decision_lines))

    action_lines = [f"# {meta['name']}：行动项", "", "## 进行中", ""]
    if active_actions:
        action_lines.extend(format_item(i) for i in active_actions)
    else:
        action_lines.extend(["暂无记录。", ""])
    closed = [i for i in ordered if i["kind"] == "action" and i["status"] in ("done", "cancelled")]
    action_lines.extend(["## 已关闭", ""])
    if closed:
        action_lines.extend(format_item(i) for i in closed[:20])
    else:
        action_lines.extend(["暂无记录。", ""])
    atomic_write(root / "ACTIONS.md", "\n".join(action_lines))

    timeline = [f"# {meta['name']}：时间线", ""]
    if ordered:
        for item in ordered:
            timeline.append(
                f"- {item['updated_at']}｜{item['kind']}｜{item['status']}｜{item['title']}：{item['content']}"
            )
    else:
        timeline.append("暂无记录。")
    timeline.append("")
    atomic_write(root / "TIMELINE.md", "\n".join(timeline))


def verify_project(args: argparse.Namespace, workspace: Path) -> None:
    key = project_key(args.project)
    root = project_dir(workspace, key)
    meta = load_meta(root)
    events = load_events(root / "ledger.jsonl")
    items = build_state(events)
    for item in items.values():
        if item["kind"] not in KINDS or item["status"] not in STATUSES:
            raise ValueError(f"项目条目值非法: {item['id']}")
    if args.rebuild:
        render(root, meta, items)
    print(f"verified_events={len(events)} items={len(items)}")


def project_status(args: argparse.Namespace, workspace: Path) -> None:
    key = project_key(args.project)
    root = project_dir(workspace, key)
    load_meta(root)
    items = build_state(load_events(root / "ledger.jsonl"))
    counts = Counter(item["kind"] for item in items.values())
    statuses = Counter(item["status"] for item in items.values())
    print(f"project={key}")
    print(f"items={len(items)}")
    for kind in KINDS:
        print(f"{kind}={counts[kind]}")
    print(f"open_or_active={sum(statuses[s] for s in ('open', 'planned', 'active', 'blocked'))}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Jarvis append-only project state")
    root.add_argument("--workspace", default=".")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--project", required=True)
    init.add_argument("--name", required=True)
    record = sub.add_parser("record")
    record.add_argument("--project", required=True)
    record.add_argument("--kind", choices=KINDS, required=True)
    record.add_argument("--title", required=True)
    record.add_argument("--content", required=True)
    record.add_argument("--source", required=True)
    record.add_argument("--owner", default="")
    record.add_argument("--due", default="")
    record.add_argument("--status", choices=STATUSES)
    change = sub.add_parser("change")
    change.add_argument("id")
    change.add_argument("--status", choices=STATUSES, required=True)
    change.add_argument("--note", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--project", required=True)
    verify.add_argument("--rebuild", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("--project", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir() or workspace == Path("/"):
        print(f"invalid workspace: {workspace}", file=sys.stderr)
        return 2
    projects_root = workspace / "knowledge/projects"
    try:
        with locked(projects_root):
            if args.command == "init":
                init_project(args, workspace)
            elif args.command == "record":
                record_item(args, workspace)
            elif args.command == "change":
                change_item(args, workspace)
            elif args.command == "verify":
                verify_project(args, workspace)
            elif args.command == "status":
                project_status(args, workspace)
    except (OSError, ValueError, KeyError) as exc:
        print(f"projectctl: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
