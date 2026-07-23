"""Provider-neutral Agent Workbench conversation orchestration."""

from __future__ import annotations

import os
import socket
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import SHORT_NAME
from ..providers.base import AgentProvider
from .context import ContextCollector
from .memory import IntentMemory
from .models import AgentEvent, ProviderTurnResult
from .policy import ToolPolicy
from .project_store import ProjectStore, process_start_identity
from .tools import ToolContext, ToolRegistry

Dispatch = Callable[[Callable[[], Any]], Any]
EventHandler = Callable[[AgentEvent], None]


class AgentSession:
    def __init__(
        self,
        *,
        app: Any,
        gui: Any,
        store: ProjectStore,
        registry: ToolRegistry,
        provider: AgentProvider,
        policy: ToolPolicy | None = None,
    ) -> None:
        self.app = app
        self.gui = gui
        self.store = store
        self.registry = registry
        self.provider = provider
        self.policy = policy or ToolPolicy()
        self._host_session_id = uuid.uuid4().hex
        self._process_started = process_start_identity(os.getpid())

    def run(
        self,
        prompt: str,
        *,
        dispatch: Dispatch | None = None,
        on_event: EventHandler | None = None,
        cancel_event: threading.Event | None = None,
        expected_document_path: str | None = None,
    ) -> ProviderTurnResult:
        clean_prompt = str(prompt).strip()
        if not clean_prompt:
            raise ValueError(f"{SHORT_NAME} prompts cannot be empty")
        if dispatch is None and self.gui is not None:
            raise RuntimeError("A GUI-thread dispatcher is required for FreeCAD sessions")
        dispatch_call = dispatch or (lambda operation: operation())
        emit = on_event or (lambda _event: None)
        cancelled = cancel_event or threading.Event()
        active_document = dispatch_call(lambda: getattr(self.app, "ActiveDocument", None))
        file_path = (
            str(getattr(active_document, "FileName", "")) if active_document is not None else ""
        )
        if not file_path:
            raise RuntimeError(
                f"Save the active FreeCAD document before starting {SHORT_NAME}"
            )
        if expected_document_path and _resolved_path(file_path) != _resolved_path(
            expected_document_path
        ):
            raise RuntimeError(
                f"The active FreeCAD document changed before this {SHORT_NAME} "
                "task started"
            )
        project = self.store.for_document(file_path)
        memory = IntentMemory(project)
        conversation_id = str(project.active_conversation()["conversation_id"])
        recent_turns = project.recent_turns(conversation_id=conversation_id)

        def collect_state() -> dict[str, Any]:
            self._require_same_document(active_document, file_path)
            return ContextCollector(
                self.app,
                self.gui,
                document=active_document,
            ).collect()

        state = dispatch_call(collect_state)
        user_turn = project.append_turn(
            "user",
            clean_prompt,
            metadata={
                "turn_owner": {
                    "host": socket.gethostname(),
                    "pid": os.getpid(),
                    "process_started": self._process_started,
                    "session_id": self._host_session_id,
                }
            },
            conversation_id=conversation_id,
        )
        specs = self.policy.exposed(self.registry.specs(), has_document=True)
        schema_revision = self.registry.schema_revision(specs)
        existing = project.provider_thread(
            self.provider.provider_id,
            conversation_id=conversation_id,
        )
        existing_thread_id = None
        if existing and existing.get("schema_revision") == schema_revision:
            existing_thread_id = existing.get("thread_id")

        allowed_names = {spec.name for spec in specs}
        tool_call_count = 0
        successful_document_writes: list[dict[str, Any]] = []

        def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            nonlocal tool_call_count
            tool_call_count += 1
            if tool_call_count > self.policy.max_tool_calls_per_turn:
                return {
                    "ok": False,
                    "tool": name,
                    "error": f"{SHORT_NAME} tool-call budget exceeded",
                }
            if name not in allowed_names:
                return {
                    "ok": False,
                    "tool": name,
                    "error": (
                        f"Tool is not available under the active {SHORT_NAME} policy"
                    ),
                }
            spec = self.registry.get(name)
            decision = self.policy.decide(spec)
            if not decision.allowed:
                return {"ok": False, "tool": name, "error": decision.reason}
            tool_context = ToolContext(
                app=self.app,
                gui=self.gui,
                project=project,
                memory=memory,
                source_turn_id=user_turn["id"],
                document=active_document,
            )

            def execute_tool() -> dict[str, Any]:
                self._require_same_document(active_document, file_path)
                payload = self.registry.call(name, arguments, tool_context)
                if payload.get("ok") and spec.mutates_document:
                    payload = self._persist_successful_write(
                        payload,
                        document=active_document,
                        project=project,
                        tool=name,
                        source_turn_id=user_turn["id"],
                    )
                return payload

            try:
                payload = dispatch_call(execute_tool)
            except Exception as exc:
                payload = {"ok": False, "tool": name, "error": str(exc)}
            if payload.get("ok") and spec.mutates_document:
                persistence = payload.get("persistence")
                if isinstance(persistence, dict):
                    successful_document_writes.append(
                        {
                            "tool": name,
                            "saved": bool(persistence.get("saved")),
                            "checkpointed": bool(persistence.get("checkpointed")),
                            "checkpoint": persistence.get("checkpoint"),
                        }
                    )
            _log_event(project, "tool_call", {"tool": name, "ok": payload.get("ok")})
            return payload

        _log_event(
            project,
            "turn_started",
            {
                "provider": self.provider.provider_id,
                "conversation_id": conversation_id,
                "source_turn_id": user_turn["id"],
            },
        )
        try:
            result = self.provider.run_turn(
                prompt=clean_prompt,
                context=state,
                memory=memory.provider_context(),
                recent_turns=recent_turns,
                tools=specs,
                schema_revision=schema_revision,
                runtime_dir=str(project.runtime_dir),
                existing_thread_id=existing_thread_id,
                tool_handler=call_tool,
                on_event=emit,
                cancel_event=cancelled,
            )
        except Exception as exc:
            _log_event(
                project,
                "turn_failed",
                {
                    "provider": self.provider.provider_id,
                    "error_type": type(exc).__name__,
                    "conversation_id": conversation_id,
                    "source_turn_id": user_turn["id"],
                    "successful_document_writes": len(successful_document_writes),
                },
            )
            failure_content = _provider_failure_content(successful_document_writes)
            project.append_turn(
                "assistant",
                failure_content,
                metadata={
                    "provider": self.provider.provider_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "successful_document_writes": successful_document_writes,
                },
                conversation_id=conversation_id,
            )
            raise
        project.set_provider_thread(
            self.provider.provider_id,
            result.thread_id,
            schema_revision,
            conversation_id=conversation_id,
        )
        project.append_turn(
            "assistant",
            result.text,
            metadata={
                "provider": self.provider.provider_id,
                "provider_turn_id": result.turn_id,
                "tool_calls": result.tool_calls,
                "status": result.status,
            },
            conversation_id=conversation_id,
        )
        _log_event(
            project,
            "turn_completed",
            {"provider": self.provider.provider_id, "tool_calls": result.tool_calls},
        )
        return result

    @staticmethod
    def _persist_successful_write(
        payload: dict[str, Any],
        *,
        document: Any,
        project: Any,
        tool: str,
        source_turn_id: str,
    ) -> dict[str, Any]:
        checkpoint: dict[str, str] | None = None
        checkpoint_error: Exception | None = None
        try:
            checkpoint = project.checkpoint_document(
                document,
                tool=tool,
                source_turn_id=source_turn_id,
            )
        except Exception as exc:
            checkpoint_error = exc
            _log_event(
                project,
                "document_checkpoint_failed",
                {"tool": tool, "error_type": type(exc).__name__},
            )

        save_error: Exception | None = None
        try:
            save = getattr(document, "save", None)
            if not callable(save):
                raise RuntimeError("FreeCAD cannot save the active document")
            if save() is False:
                raise RuntimeError("FreeCAD refused to save the active document")
        except Exception as exc:
            save_error = exc
            _log_event(
                project,
                "document_save_failed",
                {"tool": tool, "error_type": type(exc).__name__},
            )

        persistence: dict[str, Any] = {
            "saved": save_error is None,
            "checkpointed": checkpoint is not None,
            "checkpoint": checkpoint["name"] if checkpoint is not None else None,
        }
        result = {**payload, "persistence": persistence}
        if checkpoint is not None:
            _log_event(
                project,
                "document_checkpoint",
                {
                    "tool": tool,
                    "checkpoint": checkpoint["name"],
                    "saved": save_error is None,
                },
            )
        if save_error is not None:
            recovery = (
                f" A recoverable checkpoint exists as {checkpoint['name']}."
                if checkpoint is not None
                else " No crash-safe checkpoint could be created."
            )
            warning = (
                "The model edit was applied in memory, but the document file could not "
                "be saved." + recovery
            )
            persistence["warning"] = warning
            return {**result, "warning": warning}
        if checkpoint_error is not None:
            persistence["warning"] = (
                "The document was saved, but the redundant crash-safe checkpoint failed."
            )
        return result

    def project_for_active_document(self):
        state = self._document_state()
        path = str(state.get("file_path") or "")
        return self.store.for_document(path) if path else None

    def _document_state(self) -> dict[str, Any]:
        document = getattr(self.app, "ActiveDocument", None)
        return {
            "exists": document is not None,
            "file_path": str(getattr(document, "FileName", "")) if document else None,
        }

    def _require_same_document(self, document: Any, file_path: str) -> None:
        active = getattr(self.app, "ActiveDocument", None)
        active_path = str(getattr(active, "FileName", "")) if active is not None else ""
        if active is not document or active_path != file_path:
            raise RuntimeError(
                f"The active FreeCAD document changed during this {SHORT_NAME} task. "
                "Return to the original saved document and start a new task."
            )


def _resolved_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def _log_event(project: Any, kind: str, data: dict[str, Any]) -> None:
    """Keep diagnostic storage failures from changing modeling behavior."""
    try:
        project.log_event(kind, data)
    except Exception:
        return


def _provider_failure_content(writes: list[dict[str, Any]]) -> str:
    if not writes:
        return (
            f"{SHORT_NAME} could not complete the task. "
            "Review the visible error and retry."
        )
    saved = sum(bool(item.get("saved")) for item in writes)
    checkpoints = [str(item["checkpoint"]) for item in writes if item.get("checkpoint")]
    content = (
        f"{SHORT_NAME}'s response stopped after applying "
        f"{len(writes)} model edit(s). {saved} edit(s) were saved to the document. "
        "Do not retry the whole task, because that can duplicate geometry."
    )
    if checkpoints:
        content += " Crash-safe checkpoint(s): " + ", ".join(checkpoints) + "."
    return content
