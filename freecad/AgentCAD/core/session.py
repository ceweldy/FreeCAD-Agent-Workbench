"""Provider-neutral AgentCAD conversation orchestration."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..providers.base import AgentProvider
from .context import ContextCollector
from .memory import IntentMemory
from .models import AgentEvent, ProviderTurnResult
from .policy import ToolPolicy
from .project_store import ProjectStore
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
            raise ValueError("AgentCAD prompts cannot be empty")
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
            raise RuntimeError("Save the active FreeCAD document before starting AgentCAD")
        if expected_document_path and _resolved_path(file_path) != _resolved_path(
            expected_document_path
        ):
            raise RuntimeError(
                "The active FreeCAD document changed before this AgentCAD task started"
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

        def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            nonlocal tool_call_count
            tool_call_count += 1
            if tool_call_count > self.policy.max_tool_calls_per_turn:
                return {
                    "ok": False,
                    "tool": name,
                    "error": "AgentCAD tool-call budget exceeded",
                }
            if name not in allowed_names:
                return {
                    "ok": False,
                    "tool": name,
                    "error": "Tool is not available under the active AgentCAD policy",
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
                return self.registry.call(name, arguments, tool_context)

            try:
                payload = dispatch_call(execute_tool)
            except Exception as exc:
                payload = {"ok": False, "tool": name, "error": str(exc)}
            project.log_event("tool_call", {"tool": name, "ok": payload.get("ok")})
            return payload

        project.log_event("turn_started", {"provider": self.provider.provider_id})
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
            project.log_event(
                "turn_failed",
                {
                    "provider": self.provider.provider_id,
                    "error_type": type(exc).__name__,
                },
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
        project.log_event(
            "turn_completed",
            {"provider": self.provider.provider_id, "tool_calls": result.tool_calls},
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
                "The active FreeCAD document changed during this AgentCAD task. "
                "Return to the original saved document and start a new task."
            )


def _resolved_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())
