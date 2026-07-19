"""Atomic, document-scoped AgentCAD project and conversation storage."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_SCHEMA = "agentcad-project-v1"
CONVERSATION_SCHEMA = "agentcad-conversation-v1"
_PROJECT_LOCKS: dict[Path, _ProjectLock] = {}
_PROJECT_LOCKS_GUARD = threading.Lock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def project_id_for_path(document_path: str | Path) -> str:
    source = str(Path(document_path).expanduser().resolve())
    return hashlib.sha256(source.encode()).hexdigest()


def slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return clean[:60] or "freecad-project"


class AgentCADProject:
    def __init__(self, root: Path, document_path: Path) -> None:
        self.root = root.expanduser().resolve()
        self.document_path = document_path
        self.manifest_path = self.root / "project.json"
        self.conversations_dir = self.root / "conversations"
        self.memory_path = self.root / "memory.json"
        self.events_path = self.root / "events.jsonl"
        self.runtime_dir = self.root / "runtime"
        self._lock = _project_lock(self.root)
        with self._lock:
            self._ensure()

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the shared lock for a complete project read-modify-write cycle."""
        with self._lock:
            yield

    def _ensure(self) -> None:
        _private_directory(self.root.parent)
        _private_directory(self.root)
        _private_directory(self.conversations_dir)
        _private_directory(self.runtime_dir)
        for path in (
            self.manifest_path,
            self.memory_path,
            self.events_path,
            *self.conversations_dir.glob("*.json"),
        ):
            _private_file(path)
        if not self.manifest_path.exists():
            conversation_id = uuid.uuid4().hex
            manifest = {
                "schema": PROJECT_SCHEMA,
                "version": 1,
                "project_id": project_id_for_path(self.document_path),
                "document_path": str(self.document_path),
                "title": self.document_path.stem,
                "active_conversation_id": conversation_id,
                "conversations": [conversation_id],
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            self._write_json(
                self.conversation_path(conversation_id),
                _new_conversation(conversation_id),
            )
            self._write_json(self.manifest_path, manifest)
        else:
            manifest = self._read_json(self.manifest_path)
            expected_id = project_id_for_path(self.document_path)
            if (
                manifest.get("schema") != PROJECT_SCHEMA
                or manifest.get("project_id") != expected_id
                or manifest.get("document_path") != str(self.document_path)
            ):
                raise RuntimeError(
                    "AgentCAD project storage belongs to another saved document"
                )

    def manifest(self) -> dict[str, Any]:
        with self._lock:
            value = self._read_json(self.manifest_path)
            if value.get("schema") != PROJECT_SCHEMA:
                raise RuntimeError(
                    f"Unsupported AgentCAD project schema at {self.manifest_path}"
                )
            return value

    def conversation_path(self, conversation_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", conversation_id):
            raise ValueError("Invalid AgentCAD conversation id")
        return self.conversations_dir / f"{conversation_id}.json"

    def active_conversation(self) -> dict[str, Any]:
        with self._lock:
            manifest = self.manifest()
            return self.conversation(str(manifest["active_conversation_id"]))

    def conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            value = self._read_json(self.conversation_path(conversation_id))
            if value.get("schema") != CONVERSATION_SCHEMA:
                raise RuntimeError("Unsupported AgentCAD conversation schema")
            return value

    def create_conversation(self, title: str = "New conversation") -> dict[str, Any]:
        with self._lock:
            conversation_id = uuid.uuid4().hex
            conversation = _new_conversation(conversation_id, title=title)
            self._write_json(self.conversation_path(conversation_id), conversation)
            manifest = self.manifest()
            manifest["active_conversation_id"] = conversation_id
            manifest.setdefault("conversations", []).append(conversation_id)
            manifest["updated_at"] = now_iso()
            self._write_json(self.manifest_path, manifest)
            return conversation

    def activate_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            conversation = self.conversation(conversation_id)
            manifest = self.manifest()
            if conversation_id not in manifest.get("conversations", []):
                raise ValueError("Conversation does not belong to this project")
            manifest["active_conversation_id"] = conversation_id
            manifest["updated_at"] = now_iso()
            self._write_json(self.manifest_path, manifest)
            return conversation

    def append_turn(
        self,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"Unsupported conversation role: {role}")
        clean = str(content).strip()
        if not clean:
            raise ValueError("Conversation turns cannot be empty")
        with self._lock:
            conversation = self._selected_conversation(conversation_id)
            turn = {
                "id": uuid.uuid4().hex,
                "role": role,
                "content": clean,
                "created_at": now_iso(),
                "metadata": _redact(metadata or {}),
            }
            conversation["turns"].append(turn)
            if conversation.get("title") == "New conversation" and role == "user":
                conversation["title"] = clean[:72]
            conversation["updated_at"] = now_iso()
            self._write_json(
                self.conversation_path(str(conversation["conversation_id"])),
                conversation,
            )
            return turn

    def provider_thread(
        self,
        provider: str,
        conversation_id: str | None = None,
    ) -> dict[str, str] | None:
        with self._lock:
            conversation = self._selected_conversation(conversation_id)
            value = (conversation.get("provider_threads") or {}).get(provider)
            return dict(value) if isinstance(value, dict) else None

    def set_provider_thread(
        self,
        provider: str,
        thread_id: str,
        schema_revision: str,
        conversation_id: str | None = None,
    ) -> None:
        with self._lock:
            conversation = self._selected_conversation(conversation_id)
            conversation.setdefault("provider_threads", {})[provider] = {
                "thread_id": thread_id,
                "schema_revision": schema_revision,
                "updated_at": now_iso(),
            }
            conversation["updated_at"] = now_iso()
            self._write_json(
                self.conversation_path(str(conversation["conversation_id"])),
                conversation,
            )

    def recent_turns(
        self,
        limit: int = 8,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            turns = list(self._selected_conversation(conversation_id).get("turns", []))
            return turns[-max(1, limit) :]

    def _selected_conversation(self, conversation_id: str | None) -> dict[str, Any]:
        if conversation_id is None:
            return self.active_conversation()
        manifest = self.manifest()
        if conversation_id not in manifest.get("conversations", []):
            raise ValueError("Conversation does not belong to this project")
        return self.conversation(conversation_id)

    def catalog(self) -> list[dict[str, Any]]:
        with self._lock:
            manifest = self.manifest()
            result = []
            for conversation_id in manifest.get("conversations", []):
                conversation = self.conversation(str(conversation_id))
                result.append(
                    {
                        "id": conversation_id,
                        "title": conversation.get("title", "New conversation"),
                        "turn_count": len(conversation.get("turns", [])),
                        "updated_at": conversation.get("updated_at"),
                        "active": conversation_id
                        == manifest.get("active_conversation_id"),
                    }
                )
            return sorted(result, key=lambda item: str(item["updated_at"]), reverse=True)

    def read_memory_payload(self) -> dict[str, Any] | None:
        with self._lock:
            return self._read_json(self.memory_path) if self.memory_path.exists() else None

    def write_memory_payload(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._write_json(self.memory_path, payload)

    def log_event(self, kind: str, data: dict[str, Any]) -> None:
        event = json.dumps(
            {"timestamp": now_iso(), "kind": kind, "data": _redact(data)},
            sort_keys=True,
            ensure_ascii=True,
        )
        with self._lock:
            _private_directory(self.events_path.parent)
            descriptor = os.open(
                self.events_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            _private_file(self.events_path)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(event + "\n")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Cannot read AgentCAD data at {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"AgentCAD data at {path} is not an object")
        return value

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        _private_directory(path.parent)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class ProjectStore:
    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        _private_directory(self.data_root)

    def for_document(self, document_path: str | Path) -> AgentCADProject:
        raw_path = str(document_path).strip()
        if not raw_path:
            raise ValueError("A saved document path is required")
        candidate = Path(raw_path).expanduser()
        if candidate.exists() and candidate.is_dir():
            raise ValueError("A saved document file path is required, not a directory")
        path = candidate.resolve()
        project_id = project_id_for_path(path)
        root = self.data_root / "projects" / f"{slugify(path.stem)}-{project_id}"
        return AgentCADProject(root, path)


def _new_conversation(conversation_id: str, title: str = "New conversation") -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "schema": CONVERSATION_SCHEMA,
        "version": 1,
        "conversation_id": conversation_id,
        "title": title,
        "turns": [],
        "provider_threads": {},
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if any(
                marker in normalized
                for marker in (
                    "apikey",
                    "authorization",
                    "cookie",
                    "credential",
                    "password",
                    "privatekey",
                    "secret",
                    "token",
                )
            ):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _redact(item)
        return result
    if isinstance(value, list | tuple):
        return [_redact(item) for item in value]
    return value


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(path, 0o700)


def _private_file(path: Path) -> None:
    if path.is_file() and os.name == "posix":
        os.chmod(path, 0o600)


class _ProjectLock:
    """Reentrant thread and process lock backed by a project lock file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self) -> _ProjectLock:
        self._thread_lock.acquire()
        depth = int(getattr(self._local, "depth", 0))
        try:
            if depth == 0:
                self._local.descriptor = self._acquire_file_lock()
            self._local.depth = depth + 1
            return self
        except BaseException:
            self._thread_lock.release()
            raise

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        depth = int(self._local.depth) - 1
        try:
            if depth == 0:
                descriptor = int(self._local.descriptor)
                self._release_file_lock(descriptor)
                del self._local.descriptor
                del self._local.depth
            else:
                self._local.depth = depth
        finally:
            self._thread_lock.release()

    def _acquire_file_lock(self) -> int:
        _private_directory(self.path.parent)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        _private_file(self.path)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                while True:
                    try:
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _release_file_lock(descriptor: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _project_lock(root: Path) -> _ProjectLock:
    key = root.expanduser().resolve()
    with _PROJECT_LOCKS_GUARD:
        return _PROJECT_LOCKS.setdefault(key, _ProjectLock(key / ".agentcad.lock"))
