from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from freecad.AgentCAD.core.models import AgentEvent, ProviderStatus, ProviderTurnResult
from freecad.AgentCAD.core.policy import ToolPolicy
from freecad.AgentCAD.core.project_store import ProjectStore
from freecad.AgentCAD.core.session import AgentSession
from freecad.AgentCAD.core.tools import SafetyLevel, ToolRegistry, ToolSpec

from .fakes import FakeDocument, FakeGui, FakeObject, FakeShape, fake_app


def direct_dispatch(operation):
    return operation()


class RecordingProvider:
    provider_id = "recording"
    display_name = "Recording provider"

    def __init__(self, tool_to_call: tuple[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tool_to_call = tool_to_call

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.provider_id, True, True, "Ready")

    def run_turn(self, **kwargs: Any) -> ProviderTurnResult:
        captured = dict(kwargs)
        self.calls.append(captured)
        tool_calls = 0
        if self.tool_to_call is not None:
            name, arguments = self.tool_to_call
            captured["tool_result"] = kwargs["tool_handler"](name, arguments)
            tool_calls = 1
        kwargs["on_event"](AgentEvent("text_delta", "Done"))
        return ProviderTurnResult(
            text="Done",
            thread_id="provider-thread-1",
            turn_id=f"turn-{len(self.calls)}",
            status="completed",
            tool_calls=tool_calls,
        )

    def close(self) -> None:
        return


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="test.inspect",
            description="Read a deterministic marker from the active test document.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"],
                "additionalProperties": False,
            },
            safety=SafetyLevel.READ,
            handler=lambda context, arguments: {
                "ok": True,
                "name": arguments["name"],
                "source_turn_id": context.source_turn_id,
                "project_id": context.project.manifest()["project_id"],
            },
        )
    )
    registry.register(
        ToolSpec(
            name="test.delete",
            description="Represent a destructive operation that policy must never expose.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            safety=SafetyLevel.DESTRUCTIVE,
            handler=lambda _context, _arguments: {"ok": True},
        )
    )
    return registry


def _session(
    tmp_path: Path,
    provider: RecordingProvider,
    *,
    document: FakeDocument | None = None,
    policy: ToolPolicy | None = None,
) -> AgentSession:
    active = document if document is not None else FakeDocument(str(tmp_path / "model.FCStd"))
    active.Objects = [FakeObject("Box", shape=FakeShape())]
    return AgentSession(
        app=fake_app(active),
        gui=FakeGui(),
        store=ProjectStore(tmp_path / "agent-data"),
        registry=_registry(),
        provider=provider,
        policy=policy,
    )


def test_session_requires_saved_document_before_provider_call(tmp_path: Path) -> None:
    provider = RecordingProvider()
    session = _session(tmp_path, provider, document=FakeDocument(""))
    with pytest.raises(RuntimeError, match="Save the active FreeCAD document"):
        session.run("Create a box", dispatch=direct_dispatch)
    assert provider.calls == []


def test_session_rejects_blank_prompt_without_persisting_turn(tmp_path: Path) -> None:
    provider = RecordingProvider()
    session = _session(tmp_path, provider)
    with pytest.raises(ValueError, match="prompts cannot be empty"):
        session.run("  \n  ", dispatch=direct_dispatch)
    project = session.project_for_active_document()
    assert project is not None
    assert project.active_conversation()["turns"] == []
    assert provider.calls == []


def test_session_rejects_document_changed_before_worker_start(tmp_path: Path) -> None:
    provider = RecordingProvider()
    session = _session(tmp_path, provider)
    original_path = session.app.ActiveDocument.FileName
    session.app.ActiveDocument = FakeDocument(str(tmp_path / "replacement.FCStd"))

    with pytest.raises(RuntimeError, match="changed before"):
        session.run(
            "Inspect the original document",
            dispatch=direct_dispatch,
            expected_document_path=original_path,
        )

    assert provider.calls == []


def test_session_orchestrates_context_memory_history_tools_and_persistence(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(("test.inspect", {"name": "Box"}))
    session = _session(tmp_path, provider)
    events: list[AgentEvent] = []
    dispatches: list[object] = []

    def dispatch(operation):
        dispatches.append(operation)
        return operation()

    result = session.run("Inspect the current box", dispatch=dispatch, on_event=events.append)
    assert result.text == "Done"
    assert len(dispatches) >= 3
    call = provider.calls[0]
    assert call["prompt"] == "Inspect the current box"
    assert call["context"]["document"]["objects"][0]["name"] == "Box"
    assert call["memory"]["entries"] == []
    assert call["recent_turns"] == []
    assert [item.name for item in call["tools"]] == ["test.inspect"]
    assert len(call["schema_revision"]) == 64
    assert Path(call["runtime_dir"]).is_dir()
    assert call["existing_thread_id"] is None
    assert call["tool_result"]["ok"] is True
    assert call["tool_result"]["tool"] == "test.inspect"
    assert call["tool_result"]["source_turn_id"]
    assert [event.kind for event in events] == ["text_delta"]

    project = session.project_for_active_document()
    assert project is not None
    conversation = project.active_conversation()
    assert [turn["role"] for turn in conversation["turns"]] == ["user", "assistant"]
    assert conversation["turns"][1]["metadata"] == {
        "provider": "recording",
        "provider_turn_id": "turn-1",
        "status": "completed",
        "tool_calls": 1,
    }
    event_kinds = [
        line.split('"kind": "', 1)[1].split('"', 1)[0]
        for line in project.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert event_kinds == ["turn_started", "tool_call", "turn_completed"]


def test_session_reuses_provider_thread_only_for_matching_tool_schema(tmp_path: Path) -> None:
    provider = RecordingProvider()
    session = _session(tmp_path, provider)
    session.run("First request", dispatch=direct_dispatch)
    session.run("Second request", dispatch=direct_dispatch)
    assert provider.calls[0]["existing_thread_id"] is None
    assert provider.calls[1]["existing_thread_id"] == "provider-thread-1"
    assert [item["content"] for item in provider.calls[1]["recent_turns"]] == [
        "First request",
        "Done",
    ]

    project = session.project_for_active_document()
    assert project is not None
    project.set_provider_thread("recording", "old-thread", "different-schema")
    session.run("Third request", dispatch=direct_dispatch)
    assert provider.calls[2]["existing_thread_id"] is None


def test_session_tool_handler_fails_closed_for_unexposed_tool(tmp_path: Path) -> None:
    provider = RecordingProvider(("test.delete", {}))
    session = _session(tmp_path, provider)
    session.run("Delete the box", dispatch=direct_dispatch)
    call = provider.calls[0]
    assert call["tool_result"] == {
        "ok": False,
        "tool": "test.delete",
        "error": "Tool is not available under the active AgentCAD policy",
    }


def test_session_converts_tool_contract_error_to_provider_safe_result(tmp_path: Path) -> None:
    provider = RecordingProvider(("test.inspect", {"name": ""}))
    session = _session(tmp_path, provider)
    session.run("Inspect invalid target", dispatch=direct_dispatch)
    result = provider.calls[0]["tool_result"]
    assert result["ok"] is False
    assert result["tool"] == "test.inspect"
    assert "too short" in result["error"]


def test_project_for_active_document_returns_none_without_path(tmp_path: Path) -> None:
    provider = RecordingProvider()
    session = _session(tmp_path, provider, document=FakeDocument(""))
    assert session.project_for_active_document() is None


def test_session_requires_dispatcher_when_gui_is_present(tmp_path: Path) -> None:
    provider = RecordingProvider()
    session = _session(tmp_path, provider)
    with pytest.raises(RuntimeError, match="GUI-thread dispatcher"):
        session.run("Read state")
    assert isinstance(session.app, SimpleNamespace)


def test_session_enforces_provider_independent_tool_budget(tmp_path: Path) -> None:
    class RepeatingProvider(RecordingProvider):
        def run_turn(self, **kwargs: Any) -> ProviderTurnResult:
            self.calls.append(dict(kwargs))
            results = [
                kwargs["tool_handler"]("test.inspect", {"name": "Box"}) for _index in range(3)
            ]
            self.calls[-1]["tool_results"] = results
            return ProviderTurnResult(
                text="Done",
                thread_id="provider-thread-1",
                turn_id="turn-1",
                status="completed",
                tool_calls=3,
            )

    provider = RepeatingProvider()
    session = _session(
        tmp_path,
        provider,
        policy=ToolPolicy(max_tool_calls_per_turn=1),
    )
    session.run("Inspect once", dispatch=direct_dispatch)
    results = provider.calls[0]["tool_results"]
    assert results[0]["ok"] is True
    assert results[1] == {
        "ok": False,
        "tool": "test.inspect",
        "error": "AgentCAD tool-call budget exceeded",
    }
    assert results[2]["ok"] is False


def test_session_rejects_tool_call_after_active_document_changes(tmp_path: Path) -> None:
    provider = RecordingProvider()
    session = _session(tmp_path, provider)
    original_document = session.app.ActiveDocument
    replacement = FakeDocument(str(tmp_path / "other.FCStd"))

    def run_turn(**kwargs: Any) -> ProviderTurnResult:
        session.app.ActiveDocument = replacement
        provider.calls.append(dict(kwargs))
        provider.calls[-1]["tool_result"] = kwargs["tool_handler"](
            "test.inspect",
            {"name": "Box"},
        )
        return ProviderTurnResult(
            text="Stopped safely",
            thread_id="provider-thread-1",
            turn_id="turn-1",
            status="completed",
            tool_calls=1,
        )

    provider.run_turn = run_turn  # type: ignore[method-assign]
    session.run("Inspect the original document", dispatch=direct_dispatch)
    result = provider.calls[0]["tool_result"]
    assert result["ok"] is False
    assert "active FreeCAD document changed" in result["error"]
    assert session.app.ActiveDocument is replacement
    assert original_document.FileName.endswith("model.FCStd")


def test_session_completion_stays_with_conversation_that_received_prompt(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider()
    session = _session(tmp_path, provider)
    project = session.store.for_document(session.app.ActiveDocument.FileName)
    original_id = project.active_conversation()["conversation_id"]
    original_run_turn = provider.run_turn

    def run_turn(**kwargs: Any) -> ProviderTurnResult:
        project.create_conversation("Switched while running")
        return original_run_turn(**kwargs)

    provider.run_turn = run_turn  # type: ignore[method-assign]
    session.run("Keep this task together", dispatch=direct_dispatch)

    original = project.conversation(original_id)
    switched = project.active_conversation()
    assert [turn["role"] for turn in original["turns"]] == ["user", "assistant"]
    assert original["turns"][0]["content"] == "Keep this task together"
    assert original["provider_threads"]["recording"]["thread_id"] == "provider-thread-1"
    assert switched["title"] == "Switched while running"
    assert switched["turns"] == []
    assert switched["provider_threads"] == {}
