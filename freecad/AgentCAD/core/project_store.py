"""Atomic, document-scoped Agent Workbench project and conversation storage."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import SHORT_NAME

PROJECT_SCHEMA = "agentcad-project-v1"
CONVERSATION_SCHEMA = "agentcad-conversation-v1"
CHECKPOINT_LIMIT = 20
CHECKPOINT_FILENAME = re.compile(
    r"^(?P<timestamp>\d{8}T\d{12}Z)-.+-(?P<source_turn_id>[a-f0-9]{32})\.FCStd$"
)
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
        self.checkpoints_dir = self.root / "checkpoints"
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
        _private_directory(self.checkpoints_dir)
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
                    f"{SHORT_NAME} project storage belongs to another saved document"
                )

    def manifest(self) -> dict[str, Any]:
        with self._lock:
            value = self._read_json(self.manifest_path)
            if value.get("schema") != PROJECT_SCHEMA:
                raise RuntimeError(
                    f"Unsupported {SHORT_NAME} project schema at {self.manifest_path}"
                )
            return value

    def conversation_path(self, conversation_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", conversation_id):
            raise ValueError(f"Invalid {SHORT_NAME} conversation id")
        return self.conversations_dir / f"{conversation_id}.json"

    def active_conversation(self) -> dict[str, Any]:
        with self._lock:
            manifest = self.manifest()
            return self.conversation(str(manifest["active_conversation_id"]))

    def conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            value = self._read_json(self.conversation_path(conversation_id))
            if value.get("schema") != CONVERSATION_SCHEMA:
                raise RuntimeError(f"Unsupported {SHORT_NAME} conversation schema")
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

    def checkpoint_document(
        self,
        document: Any,
        *,
        tool: str,
        source_turn_id: str,
    ) -> dict[str, str]:
        """Durably save the current in-memory document without changing its identity."""
        save_copy = getattr(document, "saveCopy", None)
        if not callable(save_copy):
            raise RuntimeError("FreeCAD cannot create a crash-safe document checkpoint")
        if not re.fullmatch(r"[a-f0-9]{32}", source_turn_id):
            raise ValueError("A valid source turn id is required for document checkpoints")
        timestamp = datetime.now(UTC)
        created_at = timestamp.isoformat(timespec="microseconds")
        timestamp_slug = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        tool_slug = slugify(tool.replace(".", "-"))
        filename = f"{timestamp_slug}-{tool_slug}-{source_turn_id}.FCStd"
        final_path = self.checkpoints_dir / filename
        temporary = self.checkpoints_dir / f".{filename}.{uuid.uuid4().hex}.tmp.FCStd"
        with self._lock:
            _private_directory(self.checkpoints_dir)
            try:
                result = save_copy(str(temporary))
                if result is False:
                    raise RuntimeError("FreeCAD refused to create a document checkpoint")
                if not temporary.is_file() or temporary.stat().st_size <= 0:
                    raise RuntimeError("FreeCAD produced an empty document checkpoint")
                _private_file(temporary)
                os.replace(temporary, final_path)
                _private_file(final_path)
                _sync_file(final_path)
                _sync_directory(self.checkpoints_dir)

                record = {
                    "name": filename,
                    "created_at": created_at,
                    "tool": tool,
                    "source_turn_id": source_turn_id,
                }
                manifest = self.manifest()
                manifest["latest_checkpoint"] = record
                manifest["updated_at"] = now_iso()
                self._write_json(self.manifest_path, manifest)
                try:
                    self._prune_checkpoints(protected=final_path)
                except OSError as exc:
                    with suppress(Exception):
                        self.log_event(
                            "checkpoint_prune_failed",
                            {"error_type": type(exc).__name__},
                        )
                return record
            finally:
                temporary.unlink(missing_ok=True)

    def recover_interrupted_conversations(self) -> list[dict[str, str]]:
        """Close user-only conversations left behind by an interrupted host process."""
        recovered: list[dict[str, str]] = []
        with self._lock:
            manifest = self.manifest()
            for conversation_id in manifest.get("conversations", []):
                conversation = self.conversation(str(conversation_id))
                turns = conversation.get("turns", [])
                if not turns or turns[-1].get("role") != "user":
                    continue
                user_turn = turns[-1]
                if _turn_owner_is_live(user_turn):
                    continue
                user_turn_id = str(user_turn.get("id") or "")
                checkpoint = self._checkpoint_for_turn(manifest, user_turn_id)
                checkpoint_created_at = str(checkpoint.get("created_at") or "")
                checkpoint_name = str(checkpoint.get("name") or "")
                checkpoint_source_turn_id = str(checkpoint.get("source_turn_id") or "")
                checkpoint_matches = bool(
                    checkpoint_name
                    and checkpoint_created_at
                    and checkpoint_source_turn_id == user_turn_id
                )
                content = (
                    "The previous task was interrupted when FreeCAD closed before "
                    f"{SHORT_NAME} could finish."
                )
                if checkpoint_matches:
                    content += (
                        " The successful model edit was saved, and a crash-safe "
                        f"checkpoint is available as {checkpoint_name}."
                    )
                else:
                    content += " No completed crash-safe checkpoint was recorded for that task."
                recovered_at = now_iso()
                recovery_turn = {
                    "id": uuid.uuid4().hex,
                    "role": "assistant",
                    "content": content,
                    "created_at": recovered_at,
                    "metadata": {
                        "status": "interrupted",
                        "recovered_after_restart": True,
                        "source_turn_id": user_turn_id,
                        "checkpoint": checkpoint_name if checkpoint_matches else None,
                    },
                }
                turns.append(recovery_turn)
                conversation["updated_at"] = recovered_at
                self._write_json(
                    self.conversation_path(str(conversation["conversation_id"])),
                    conversation,
                )
                item = {
                    "conversation_id": str(conversation["conversation_id"]),
                    "source_turn_id": user_turn_id,
                    "checkpoint": checkpoint_name if checkpoint_matches else "",
                }
                recovered.append(item)
                self.log_event("turn_recovered", item)
        return recovered

    def _checkpoint_for_turn(
        self,
        manifest: dict[str, Any],
        source_turn_id: str,
    ) -> dict[str, str]:
        candidates: list[dict[str, str]] = []
        latest = manifest.get("latest_checkpoint")
        if isinstance(latest, dict):
            name = str(latest.get("name") or "")
            if (
                str(latest.get("source_turn_id") or "") == source_turn_id
                and name
                and (self.checkpoints_dir / name).is_file()
            ):
                candidates.append({str(key): str(value) for key, value in latest.items()})
        for path in self.checkpoints_dir.glob("*.FCStd"):
            record = _checkpoint_record_from_path(path)
            if record is not None and record["source_turn_id"] == source_turn_id:
                candidates.append(record)
        if not candidates:
            return {}
        return max(candidates, key=lambda item: (item.get("created_at", ""), item["name"]))

    def _prune_checkpoints(self, *, protected: Path | None = None) -> None:
        checkpoints = sorted(self.checkpoints_dir.glob("*.FCStd"), reverse=True)
        retained: list[Path] = []
        if protected is not None and protected in checkpoints:
            retained.append(protected)
        for checkpoint in checkpoints:
            if checkpoint == protected:
                continue
            if len(retained) < CHECKPOINT_LIMIT:
                retained.append(checkpoint)
                continue
            checkpoint.unlink(missing_ok=True)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Cannot read {SHORT_NAME} data at {path}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"{SHORT_NAME} data at {path} is not an object")
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


def _sync_directory(path: Path) -> None:
    """Persist a published directory entry on POSIX filesystems."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_file(path: Path) -> None:
    """Persist a published file using a descriptor writable by Windows _commit."""
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _checkpoint_record_from_path(path: Path) -> dict[str, str] | None:
    match = CHECKPOINT_FILENAME.fullmatch(path.name)
    try:
        valid_file = path.is_file() and path.stat().st_size > 0
    except OSError:
        return None
    if match is None or not valid_file:
        return None
    try:
        timestamp = datetime.strptime(
            match.group("timestamp"), "%Y%m%dT%H%M%S%fZ"
        ).replace(tzinfo=UTC)
    except ValueError:
        return None
    return {
        "name": path.name,
        "created_at": timestamp.isoformat(timespec="microseconds"),
        "tool": "",
        "source_turn_id": match.group("source_turn_id"),
    }


def _turn_owner_is_live(turn: dict[str, Any]) -> bool:
    metadata = turn.get("metadata")
    owner = metadata.get("turn_owner") if isinstance(metadata, dict) else None
    if not isinstance(owner, dict) or str(owner.get("host") or "") != socket.gethostname():
        return False
    try:
        pid = int(owner.get("pid"))
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if not _process_is_live(pid):
        return False
    expected_start = str(owner.get("process_started") or "")
    if not expected_start:
        return True
    actual_start = process_start_identity(pid)
    return actual_start is None or actual_start == expected_start


def _process_is_live(pid: int) -> bool:
    if os.name == "nt":
        live, _started = _windows_process_info(pid)
        return live
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def process_start_identity(pid: int) -> str | None:
    """Return a stable process-start marker without signaling the process."""
    if os.name == "nt":
        _live, started = _windows_process_info(pid)
        return started
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            fields = proc_stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            return f"proc:{fields[19]}"
        except (IndexError, OSError):
            return None
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started = completed.stdout.strip()
    return f"ps:{started}" if completed.returncode == 0 and started else None


def _windows_process_info(pid: int) -> tuple[bool, str | None]:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied, None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return True, None
        value = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return True, f"win:{value}"
    finally:
        kernel32.CloseHandle(handle)


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
