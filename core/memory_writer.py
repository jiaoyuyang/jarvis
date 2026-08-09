"""Persist confirmed Memory 2.0 candidates with backups and optimistic locking."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from typing import Any, Iterator

from core.paths import DATA_DIR, MEMORY_ROOT, PROJECT_ROOT

DEFAULT_CANDIDATE_PATH = DATA_DIR / "memory_candidates.json"
BACKUP_ROOT = PROJECT_ROOT / "backups"
FALLBACK_BACKUP_ROOT = DATA_DIR / "memory_backups"
PENDING_TTL_HOURS = 24
INTAKE_TTL_HOURS = 1
ACTIVE_STATUSES = {"PENDING", "SAVED", "CONFIRMED"}


class MemoryStoreError(RuntimeError):
    """Raised when the candidate store cannot be read safely."""


class MemoryConflictError(RuntimeError):
    """Raised when a MERGE/REPLACE target changed after candidate creation."""


class MemoryCandidateStore:
    def __init__(self, path: str | Path = DEFAULT_CANDIDATE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def create(self, candidate: dict[str, Any], session_id: str = "") -> dict[str, Any]:
        now = self._now()
        with self._locked_records() as records:
            self._expire_records(records, now)
            duplicate = self._find_duplicate(records, candidate)
            if duplicate:
                result = dict(duplicate)
                result["operation"] = "duplicate"
                result["_created"] = False
                result["_duplicate_scope"] = "pending" if duplicate.get("status") == "PENDING" else "saved"
                return result

            record = {
                "candidate_id": uuid.uuid4().hex,
                "schema_version": int(candidate.get("schema_version") or 2),
                "memory_type": candidate.get("memory_type") or "user_preference",
                "category": candidate.get("category") or "用户偏好",
                "namespace": candidate.get("namespace") or "user.preferences.general",
                "target_file": candidate["target_file"],
                "raw_content": candidate.get("raw_content") or candidate.get("content") or "",
                "normalized_content": candidate.get("normalized_content") or candidate.get("content") or "",
                # Keep content for backwards compatibility and human inspection.
                "content": candidate.get("normalized_content") or candidate.get("content") or "",
                "operation": candidate.get("operation") or "create",
                "reason": candidate.get("reason") or "",
                "created_time": now.isoformat(timespec="seconds"),
                "expires_at": (now + timedelta(hours=PENDING_TTL_HOURS)).isoformat(timespec="seconds"),
                "status": "PENDING",
                "session_id": session_id,
                "existing_memory_ref": candidate.get("existing_memory_ref"),
                "existing_memory_excerpt": candidate.get("existing_memory_excerpt"),
                "existing_memory_hash": candidate.get("existing_memory_hash"),
                "supersedes": list(candidate.get("supersedes") or []),
                "source_type": candidate.get("source_type") or "explicit_user_instruction",
                "confidence": float(candidate.get("confidence") or 0.0),
                "dedupe_key": candidate.get("dedupe_key") or "",
                "subject_key": candidate.get("subject_key") or "",
            }
            records.append(record)
            self._save_unlocked(records)
            result = dict(record)
            result["_created"] = True
            return result

    def create_intake(self, session_id: str, source_text: str, *, ttl_hours: int = INTAKE_TTL_HOURS) -> dict[str, Any]:
        now = self._now()
        with self._locked_records() as records:
            self._expire_records(records, now)
            for record in reversed(records):
                if record.get("status") == "INTAKE_ACTIVE" and record.get("session_id", "") == session_id:
                    result = dict(record)
                    result["_created"] = False
                    return result
            record = {
                "candidate_id": uuid.uuid4().hex,
                "schema_version": 2,
                "memory_type": "memory_intake_request",
                "category": "记忆摄取会话",
                "namespace": "runtime.memory_intake",
                "target_file": "",
                "raw_content": source_text,
                "normalized_content": "",
                "content": "",
                "operation": "ignore",
                "reason": "等待用户上传文件后再从文件内容生成候选",
                "created_time": now.isoformat(timespec="seconds"),
                "expires_at": (now + timedelta(hours=max(1, ttl_hours))).isoformat(timespec="seconds"),
                "status": "INTAKE_ACTIVE",
                "session_id": session_id,
                "source_type": "explicit_user_instruction",
                "confidence": 1.0,
            }
            records.append(record)
            self._save_unlocked(records)
            result = dict(record)
            result["_created"] = True
            return result

    def pending(self, session_id: str = "") -> dict[str, Any] | None:
        now = self._now()
        with self._locked_records() as records:
            changed = self._expire_records(records, now)
            if changed:
                self._save_unlocked(records)
            for record in reversed(records):
                if record.get("status") == "PENDING" and record.get("session_id", "") == session_id:
                    return dict(record)
        return None

    def pending_all(self, session_id: str = "") -> list[dict[str, Any]]:
        now = self._now()
        with self._locked_records() as records:
            changed = self._expire_records(records, now)
            if changed:
                self._save_unlocked(records)
            return [
                dict(record) for record in records
                if record.get("status") == "PENDING" and record.get("session_id", "") == session_id
            ]

    def active_intake(self, session_id: str = "") -> dict[str, Any] | None:
        now = self._now()
        with self._locked_records() as records:
            changed = self._expire_records(records, now)
            if changed:
                self._save_unlocked(records)
            for record in reversed(records):
                if record.get("status") == "INTAKE_ACTIVE" and record.get("session_id", "") == session_id:
                    return dict(record)
        return None

    def update_status(self, candidate_id: str, status: str, **fields: Any) -> dict[str, Any] | None:
        with self._locked_records() as records:
            for record in records:
                if record.get("candidate_id") == candidate_id:
                    record["status"] = status
                    record.update(fields)
                    self._save_unlocked(records)
                    return dict(record)
        return None

    def _find_duplicate(self, records: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
        candidate_key = str(candidate.get("dedupe_key") or "")
        candidate_text = self._canonical(candidate.get("normalized_content") or candidate.get("content") or "")
        for record in reversed(records):
            if record.get("status") not in ACTIVE_STATUSES:
                continue
            existing_key = str(record.get("dedupe_key") or "")
            existing_text = self._canonical(
                record.get("normalized_content") or record.get("content") or record.get("raw_content") or ""
            )
            if candidate_key and existing_key and candidate_key == existing_key:
                return record
            if candidate_text and existing_text and candidate_text == existing_text:
                return record
        return None

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryStoreError(f"cannot safely read memory candidate store: {exc}") from exc
        if not isinstance(data, list):
            raise MemoryStoreError("memory candidate store must be a JSON list")
        return [record for record in data if isinstance(record, dict)]

    @contextmanager
    def _locked_records(self) -> Iterator[list[dict[str, Any]]]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            records = self._load()
            try:
                yield records
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _save(self, records: list[dict[str, Any]]) -> None:
        with self._locked_records() as current:
            current[:] = records
            self._save_unlocked(current)

    def _save_unlocked(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(records, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _expire_records(records: list[dict[str, Any]], now: datetime) -> bool:
        changed = False
        for record in records:
            if record.get("status") not in {"PENDING", "INTAKE_ACTIVE"}:
                continue
            expires_at = MemoryCandidateStore._parse_time(record.get("expires_at"))
            if expires_at is None and record.get("created_time"):
                created = MemoryCandidateStore._parse_time(record.get("created_time"))
                hours = INTAKE_TTL_HOURS if record.get("status") == "INTAKE_ACTIVE" else PENDING_TTL_HOURS
                expires_at = created + timedelta(hours=hours) if created else None
            if expires_at and expires_at <= now:
                record["status"] = "EXPIRED"
                record["expired_time"] = now.isoformat(timespec="seconds")
                changed = True
        return changed

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return parsed.astimezone()

    @staticmethod
    def _now() -> datetime:
        return datetime.now().astimezone()

    @staticmethod
    def _canonical(text: Any) -> str:
        value = str(text or "").lower()
        return "".join(character for character in value if character.isalnum() or "\u4e00" <= character <= "\u9fff")


class MemoryWriter:
    def __init__(
        self,
        memory_root: str | Path = MEMORY_ROOT,
        backup_root: str | Path = BACKUP_ROOT,
        fallback_backup_root: str | Path = FALLBACK_BACKUP_ROOT,
    ):
        self.memory_root = Path(memory_root).resolve()
        self.backup_root = Path(backup_root)
        self.fallback_backup_root = Path(fallback_backup_root)

    def write(self, candidate: dict[str, Any]) -> dict[str, Any]:
        operation = str(candidate.get("operation") or "create").lower()
        if operation == "duplicate":
            return {
                "status": "DUPLICATE",
                "operation": operation,
                "target_file": candidate.get("target_file"),
                "backup_dir": None,
            }

        target = (self.memory_root / str(candidate["target_file"])).resolve()
        if self.memory_root not in target.parents:
            raise ValueError("memory write target is outside memory root")
        if not target.is_file():
            raise FileNotFoundError(f"memory target not found: {target}")

        original_text = target.read_text(encoding="utf-8")
        backup_dir = self._backup_target(target)
        normalized_content = str(
            candidate.get("normalized_content") or candidate.get("content") or ""
        ).strip()
        if not normalized_content:
            raise ValueError("normalized memory content is empty")

        if operation == "create":
            updated_text = self._append_record(original_text, candidate, normalized_content)
        elif operation in {"merge", "replace"}:
            updated_text = self._replace_existing(original_text, candidate, normalized_content)
        else:
            raise ValueError(f"unsupported memory operation: {operation}")

        self._atomic_write(target, updated_text)
        return {
            "status": "SAVED",
            "operation": operation,
            "target_file": str(candidate["target_file"]),
            "backup_dir": str(backup_dir),
        }

    def _backup_target(self, target: Path) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        errors: list[str] = []
        roots: list[Path] = []
        for root in (self.backup_root, self.fallback_backup_root):
            if root not in roots:
                roots.append(root)
        for root in roots:
            backup_dir = root / f"before_memory_write_{timestamp}"
            backup_target = backup_dir / target.relative_to(self.memory_root)
            try:
                backup_target.parent.mkdir(parents=True, exist_ok=False)
                shutil.copy2(target, backup_target)
                return backup_dir
            except OSError as exc:
                errors.append(f"{root}: {exc}")
                shutil.rmtree(backup_dir, ignore_errors=True)
        raise OSError("unable to create memory backup; " + " | ".join(errors))

    @staticmethod
    def _append_record(original_text: str, candidate: dict[str, Any], content: str) -> str:
        date_header = datetime.now().date().isoformat()
        memory_id = str(candidate.get("candidate_id") or uuid.uuid4().hex)
        separator = "" if original_text.endswith("\n") else "\n"
        return (
            f"{original_text}{separator}\n"
            f"<!-- memory_id:{memory_id} schema:2 -->\n"
            f"## {date_header}\n\n{content}\n"
        )

    @staticmethod
    def _replace_existing(original_text: str, candidate: dict[str, Any], content: str) -> str:
        excerpt = str(candidate.get("existing_memory_excerpt") or "")
        expected_hash = str(candidate.get("existing_memory_hash") or "")
        if not excerpt or not expected_hash:
            raise MemoryConflictError("MERGE/REPLACE requires an existing excerpt and hash")
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != expected_hash:
            raise MemoryConflictError("candidate excerpt hash is invalid")
        count = original_text.count(excerpt)
        if count != 1:
            raise MemoryConflictError(f"existing memory excerpt match count changed: {count}")
        return original_text.replace(excerpt, content, 1)

    @staticmethod
    def _atomic_write(target: Path, text: str) -> None:
        payload = text.encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
