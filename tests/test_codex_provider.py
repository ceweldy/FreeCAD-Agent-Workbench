from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from freecad.AgentCAD.core.models import AgentEvent, ProviderStatus
from freecad.AgentCAD.core.tools import SafetyLevel, ToolSpec
from freecad.AgentCAD.providers import codex as codex_module
from freecad.AgentCAD.providers.codex import (
    CODEX_CONFIG_OVERRIDES,
    CODEX_PERMISSION_PROFILE,
    CodexProtocolError,
    CodexProvider,
    _discover_codex_executable,
    _final_text,
    _item_label,
    _parse_codex_version,
    _prepare_isolated_codex_home,
    _turn_prompt,
)


class CallbackStdin:
    def __init__(self, provider: CodexProvider, result: dict[str, Any] | None = None) -> None:
        self.provider = provider
        self.result = result or {"accepted": True}
        self.messages: list[dict[str, Any]] = []
        self.flushes = 0
        self.closed = False

    def write(self, value: str) -> int:
        message = json.loads(value)
        self.messages.append(message)
        if "id" in message and "method" in message:
            self.provider._pending[int(message["id"])].put(
                {"id": message["id"], "result": self.result}
            )
        return len(value)

    def flush(self) -> None:
        self.flushes += 1

    def close(self) -> None:
        self.closed = True


class RunningProcess:
    def __init__(self, stdin: Any) -> None:
        self.stdin = stdin
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.terminated = False
        self.killed = False

    @staticmethod
    def poll() -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    @staticmethod
    def wait(timeout: float) -> int:
        assert timeout == 3.0
        return 0

    def kill(self) -> None:
        self.killed = True


def _tool() -> ToolSpec:
    return ToolSpec(
        name="agentcad.health",
        description="Return a deterministic AgentCAD protocol health check result.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        safety=SafetyLevel.READ,
        handler=lambda _context, _arguments: {"ok": True},
        requires_document=False,
    )


def test_executable_precedence_and_missing_override(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENTCAD_CODEX_BIN", str(executable))
    assert CodexProvider().executable() == str(executable)
    assert CodexProvider(str(tmp_path / "missing")).executable() is None


def test_desktop_discovery_finds_codex_outside_path(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    executable = home / ".codex" / "bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(codex_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(codex_module.Path, "home", lambda: home)
    assert _discover_codex_executable() == str(executable)


def test_desktop_discovery_honors_codex_cli_path(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "desktop" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("CODEX_CLI_PATH", str(executable))
    monkeypatch.setattr(codex_module.shutil, "which", lambda _name: None)

    assert _discover_codex_executable() == str(executable)


def test_desktop_discovery_checks_openai_windows_location(
    tmp_path: Path,
    monkeypatch,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    executable_name = "codex.exe" if os.name == "nt" else "codex"
    expected = (
        local_app_data
        / "Programs"
        / "OpenAI"
        / "Codex"
        / "bin"
        / executable_name
    )
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(codex_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(codex_module.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(codex_module, "_is_executable", lambda path: path == expected)

    assert _discover_codex_executable() == str(expected)


def test_desktop_discovery_checks_windows_npm_wrapper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_data = tmp_path / "AppData" / "Roaming"
    expected = app_data / "npm" / "codex.cmd"
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("APPDATA", str(app_data))
    monkeypatch.setattr(codex_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(codex_module.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(codex_module, "_is_executable", lambda path: path == expected)

    assert _discover_codex_executable() == str(expected)


def test_desktop_discovery_ignores_non_executable_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    candidate = home / ".codex" / "bin" / "codex"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("not executable", encoding="utf-8")
    candidate.chmod(0o600)
    monkeypatch.setattr(codex_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(codex_module.Path, "home", lambda: home)
    real_is_executable = codex_module._is_executable
    monkeypatch.setattr(
        codex_module,
        "_is_executable",
        lambda path: real_is_executable(path) if path == candidate else False,
    )
    assert _discover_codex_executable() is None


@pytest.mark.parametrize(
    ("login_output", "authenticated"),
    [
        ("Logged in using ChatGPT", True),
        ("Authenticated with API key", True),
        ("Not logged in", False),
        ("Login required", False),
    ],
)
def test_status_parses_cli_without_credentials(
    tmp_path: Path, monkeypatch, login_output: str, authenticated: bool
) -> None:
    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    def fake_status(
        command: list[str], *, env: dict[str, str] | None = None
    ) -> tuple[bool, str]:
        assert env is None or env["CODEX_HOME"]
        return (True, "codex-cli 1.2.3") if command[-1] == "--version" else (True, login_output)

    monkeypatch.setattr(codex_module, "_run_status", fake_status)
    status = CodexProvider(str(executable), state_dir=tmp_path / "state").status()
    message = (
        "Codex is connected."
        if authenticated
        else (
            "Codex is installed but Agent Workbench's isolated Codex home is not "
            "connected. Choose Connect in the Agent Workbench panel."
        )
    )
    assert status == ProviderStatus(
        "codex",
        True,
        authenticated,
        message,
        "codex-cli 1.2.3",
    )


def test_status_reports_missing_cli() -> None:
    status = CodexProvider("/definitely/not/codex").status()
    assert status.available is False
    assert status.authenticated is False
    assert "not found" in status.message


@pytest.mark.parametrize(
    "version_output",
    ["codex-cli 0.137.9", "codex-cli 0.138.0-alpha.1", "unknown build"],
)
def test_status_rejects_cli_without_permission_profile_support(
    tmp_path: Path, monkeypatch, version_output: str
) -> None:
    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_status(
        command: list[str], *, env: dict[str, str] | None = None
    ) -> tuple[bool, str]:
        assert env is None
        calls.append(command)
        return True, version_output

    monkeypatch.setattr(codex_module, "_run_status", fake_status)
    status = CodexProvider(str(executable)).status()
    assert status.available is True
    assert status.authenticated is False
    assert "0.138.0 or newer" in status.message
    assert calls == [[str(executable), "--version"]]


def test_request_writes_jsonl_without_jsonrpc_field_and_cleans_pending() -> None:
    provider = CodexProvider()
    stdin = CallbackStdin(provider, {"thread": {"id": "thread-1"}})
    provider._process = RunningProcess(stdin)
    result = provider._request("thread/start", {"cwd": "/tmp/runtime"}, timeout=0.1)
    assert result == {"thread": {"id": "thread-1"}}
    assert stdin.messages == [
        {"method": "thread/start", "id": 1, "params": {"cwd": "/tmp/runtime"}}
    ]
    assert "jsonrpc" not in stdin.messages[0]
    assert stdin.flushes == 1
    assert provider._pending == {}


def test_request_surfaces_app_server_error() -> None:
    provider = CodexProvider()

    class ErrorStdin(CallbackStdin):
        def write(self, value: str) -> int:
            message = json.loads(value)
            self.messages.append(message)
            self.provider._pending[int(message["id"])].put(
                {"id": message["id"], "error": {"message": "bad request"}}
            )
            return len(value)

    stdin = ErrorStdin(provider)
    provider._process = RunningProcess(stdin)
    with pytest.raises(CodexProtocolError, match="thread/start: bad request"):
        provider._request("thread/start", {}, timeout=0.1)
    assert provider._pending == {}


def test_thread_start_uses_isolated_dynamic_tool_contract(tmp_path: Path, monkeypatch) -> None:
    provider = CodexProvider(model="gpt-test")
    calls: list[tuple[str, dict[str, Any]]] = []

    def request(method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        calls.append((method, params))
        assert timeout == provider.request_timeout
        return {"thread": {"id": "thread-new"}}

    monkeypatch.setattr(provider, "_request", request)
    result = provider._resume_or_start_thread(
        existing_thread_id=None,
        runtime_dir=tmp_path,
        tools=[_tool()],
    )
    assert result == "thread-new"
    method, params = calls[0]
    assert method == "thread/start"
    assert params["cwd"] == str(tmp_path)
    assert params["permissions"] == CODEX_PERMISSION_PROFILE
    assert params["approvalPolicy"] == "never"
    assert params["runtimeWorkspaceRoots"] == [str(tmp_path)]
    assert params["ephemeral"] is False
    assert params["model"] == "gpt-test"
    assert params["dynamicTools"] == [_tool().dynamic_tool()]
    assert "Do not run shell" in params["developerInstructions"]


def test_app_server_command_defines_isolated_profile_and_sanitizes_environment(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    source_auth = source_home / "auth.json"
    source_auth.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    dedicated_auth = state_dir / "auth.json"
    dedicated_auth.write_text('{"dedicated": true}', encoding="utf-8")
    provider = CodexProvider(str(executable), state_dir=state_dir)
    captured: dict[str, Any] = {}

    def process_factory(command: list[str], **kwargs: Any):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return RunningProcess(CallbackStdin(provider))

    provider.process_factory = process_factory
    monkeypatch.setenv("UNRELATED_PRIVATE_VALUE", "must-not-be-inherited")
    monkeypatch.setenv("userprofile", "C:\\Users\\fixture")
    monkeypatch.setenv("https_proxy", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setattr(provider, "_notify", lambda _method, _params: None)
    provider._ensure_started()
    command = captured["command"]
    assert command[:4] == [str(executable), "app-server", "--listen", "stdio://"]
    for override in CODEX_CONFIG_OVERRIDES:
        assert ["-c", override] == command[
            command.index(override) - 1 : command.index(override) + 1
        ]
    environment = captured["kwargs"]["env"]
    assert "UNRELATED_PRIVATE_VALUE" not in environment
    assert environment["https_proxy"] == "http://proxy.example:8080"
    assert environment["NO_PROXY"] == "localhost,127.0.0.1"
    assert environment.get("HOME")
    assert environment["userprofile"] == "C:\\Users\\fixture"
    isolated_home = Path(environment["CODEX_HOME"])
    assert isolated_home == tmp_path / "state"
    assert isolated_home != source_home
    assert not (isolated_home / "auth.json").samefile(source_auth)
    assert (isolated_home / "auth.json").read_text(encoding="utf-8") == (
        '{"dedicated": true}'
    )
    assert (isolated_home / "config.toml").read_text(encoding="utf-8").startswith(
        "# Managed by Agent Workbench."
    )
    session_marker = isolated_home / "sessions" / "thread-1"
    session_marker.parent.mkdir()
    session_marker.write_text("persistent", encoding="utf-8")
    provider.close()
    assert isolated_home.exists()
    restarted = CodexProvider(str(executable), state_dir=tmp_path / "state")
    assert restarted._isolated_home() == isolated_home
    assert session_marker.read_text(encoding="utf-8") == "persistent"


def test_status_checks_authentication_inside_isolated_home(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_status(
        command: list[str], *, env: dict[str, str] | None = None
    ) -> tuple[bool, str]:
        calls.append((command, env))
        if command[-1] == "--version":
            return True, "codex-cli 1.2.3"
        return True, "Logged in using ChatGPT"

    monkeypatch.setattr(codex_module, "_run_status", fake_status)
    provider = CodexProvider(str(executable), state_dir=tmp_path / "agentcad-home")
    assert provider.status().authenticated is True
    login_command, login_environment = calls[1]
    assert login_command == [
        str(executable),
        "login",
        "status",
        "-c",
        'cli_auth_credentials_store="file"',
    ]
    assert login_environment is not None
    assert login_environment["CODEX_HOME"] == str(tmp_path / "agentcad-home")


def test_prepare_isolated_home_removes_legacy_shared_auth_link(
    tmp_path: Path, monkeypatch
) -> None:
    source_home = tmp_path / "source"
    source_home.mkdir()
    source_auth = source_home / "auth.json"
    source_auth.write_text("{}", encoding="utf-8")
    target = tmp_path / "agentcad"
    target.mkdir()
    (target / "auth.json").symlink_to(source_auth)
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    assert _prepare_isolated_codex_home(target) == target.resolve()
    assert not (target / "auth.json").exists()


def test_chatgpt_login_uses_app_server_and_returns_challenge(monkeypatch) -> None:
    provider = CodexProvider()
    calls: list[tuple[str, dict[str, Any]]] = []
    responses = iter(
        [
            {"account": None, "requiresOpenaiAuth": True},
            {
                "type": "chatgpt",
                "loginId": "login-1",
                "authUrl": "https://chatgpt.com/agentcad-login",
            },
            {
                "account": {"type": "chatgpt", "planType": "plus"},
                "requiresOpenaiAuth": True,
            },
        ]
    )

    def request(
        method: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        assert timeout == provider.request_timeout
        calls.append((method, params))
        response = next(responses)
        if method == "account/login/start":
            provider._notifications.put(
                {
                    "method": "account/login/completed",
                    "params": {
                        "loginId": "login-1",
                        "success": True,
                        "error": None,
                    },
                }
            )
        return response

    monkeypatch.setattr(provider, "_ensure_started", lambda: None)
    monkeypatch.setattr(provider, "_request", request)
    challenges: list[dict[str, str]] = []
    status = provider.login_chatgpt(
        on_challenge=challenges.append,
        cancel_event=threading.Event(),
    )
    assert status.ready is True
    assert challenges == [
        {
            "login_id": "login-1",
            "auth_url": "https://chatgpt.com/agentcad-login",
        }
    ]
    assert calls == [
        ("account/read", {"refreshToken": False}),
        ("account/login/start", {"type": "chatgpt"}),
        ("account/read", {"refreshToken": False}),
    ]


def test_chatgpt_login_surfaces_failed_completion(monkeypatch) -> None:
    provider = CodexProvider()
    monkeypatch.setattr(provider, "_ensure_started", lambda: None)
    responses = iter(
        [
            {"account": None, "requiresOpenaiAuth": True},
            {
                "type": "chatgpt",
                "loginId": "login-2",
                "authUrl": "https://chatgpt.com/agentcad-login",
            },
        ]
    )
    def request(method: str, _params: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        response = next(responses)
        if method == "account/login/start":
            provider._notifications.put(
                {
                    "method": "account/login/completed",
                    "params": {
                        "loginId": "login-2",
                        "success": False,
                        "error": "login denied",
                    },
                }
            )
        return response

    monkeypatch.setattr(provider, "_request", request)
    with pytest.raises(CodexProtocolError, match="login denied"):
        provider.login_chatgpt(
            on_challenge=lambda _challenge: None,
            cancel_event=threading.Event(),
        )


def test_provider_rejects_overlapping_turns() -> None:
    provider = CodexProvider()
    provider._turn_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            provider.run_turn(
                prompt="Inspect",
                context={},
                memory={},
                recent_turns=[],
                tools=[],
                schema_revision="schema",
                runtime_dir="/tmp/agentcad-test-runtime",
                existing_thread_id=None,
                tool_handler=lambda _name, _arguments: {"ok": True},
                on_event=lambda _event: None,
                cancel_event=threading.Event(),
            )
    finally:
        provider._turn_lock.release()


def test_turn_event_failure_is_advisory_and_handlers_are_cleared(
    tmp_path: Path, monkeypatch
) -> None:
    provider = CodexProvider()
    interrupted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        provider,
        "status",
        lambda: ProviderStatus("codex", True, True, "Codex is connected."),
    )
    monkeypatch.setattr(provider, "_ensure_started", lambda: None)
    monkeypatch.setattr(
        provider,
        "_resume_or_start_thread",
        lambda **_kwargs: "thread-1",
    )
    monkeypatch.setattr(
        provider,
        "_request",
        lambda _method, _params, **_kwargs: {"turn": {"id": "turn-1"}},
    )
    monkeypatch.setattr(
        provider,
        "_interrupt_and_close",
        lambda thread_id, turn_id: interrupted.append((thread_id, turn_id)),
    )
    monkeypatch.setattr(
        provider,
        "_wait_for_turn",
        lambda **_kwargs: codex_module.ProviderTurnResult(
            text="Completed",
            thread_id="thread-1",
            turn_id="turn-1",
            status="completed",
        ),
    )

    def reject_event(_event: AgentEvent) -> None:
        raise RuntimeError("panel was destroyed")

    result = provider._run_turn_locked(
        prompt="Inspect the model",
        context={},
        memory={},
        recent_turns=[],
        tools=[_tool()],
        schema_revision="schema",
        runtime_dir=str(tmp_path),
        existing_thread_id=None,
        tool_handler=lambda _name, _arguments: {"ok": True},
        on_event=reject_event,
        cancel_event=threading.Event(),
    )
    assert result.text == "Completed"
    assert interrupted == []
    assert any("event delivery failed" in item for item in provider._stderr)
    assert provider._tool_handler is None
    assert provider._event_handler is None
    assert provider._transport_tools == {}


def test_failed_app_server_initialization_closes_process(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    provider = CodexProvider(str(executable), state_dir=tmp_path / "state")
    process = RunningProcess(io.StringIO())
    provider.process_factory = lambda _command, **_kwargs: process
    monkeypatch.setattr(
        provider,
        "_request",
        lambda _method, _params, **_kwargs: (_ for _ in ()).throw(
            CodexProtocolError("initialize failed")
        ),
    )
    with pytest.raises(CodexProtocolError, match="initialize failed"):
        provider._ensure_started()
    assert process.terminated is True
    assert provider._process is None


def test_thread_resume_falls_back_to_start_on_protocol_error(
    tmp_path: Path, monkeypatch
) -> None:
    provider = CodexProvider()
    methods: list[str] = []

    def request(method: str, _params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        methods.append(method)
        assert timeout == provider.request_timeout
        if method == "thread/resume":
            raise CodexProtocolError("missing thread")
        return {"thread": {"id": "replacement"}}

    monkeypatch.setattr(provider, "_request", request)
    assert (
        provider._resume_or_start_thread(
            existing_thread_id="old",
            runtime_dir=tmp_path,
            tools=[_tool()],
        )
        == "replacement"
    )
    assert methods == ["thread/resume", "thread/start"]


@pytest.mark.parametrize(
    ("method", "decision"),
    [
        ("item/commandExecution/requestApproval", "decline"),
        ("item/fileChange/requestApproval", "decline"),
        ("applyPatchApproval", "denied"),
        ("execCommandApproval", "denied"),
    ],
)
def test_server_approval_requests_fail_closed(method: str, decision: str, monkeypatch) -> None:
    provider = CodexProvider()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_send", sent.append)
    provider._handle_server_request({"id": 9, "method": method, "params": {}})
    assert sent == [{"id": 9, "result": {"decision": decision}}]


def test_permission_expansion_request_returns_empty_turn_scope(monkeypatch) -> None:
    provider = CodexProvider()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_send", sent.append)
    provider._handle_server_request(
        {
            "id": 91,
            "method": "item/permissions/requestApproval",
            "params": {"permissions": {"network": True, "fileSystem": "write"}},
        }
    )
    assert sent == [{"id": 91, "result": {"permissions": {}, "scope": "turn"}}]


def test_unknown_server_request_is_rejected(monkeypatch) -> None:
    provider = CodexProvider()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_send", sent.append)
    provider._handle_server_request({"id": 10, "method": "account/read", "params": {}})
    assert sent[0]["id"] == 10
    assert sent[0]["error"]["code"] == -32601
    assert "does not permit" in sent[0]["error"]["message"]


def test_dynamic_tool_response_and_events_match_app_server_contract(monkeypatch) -> None:
    provider = CodexProvider()
    provider._transport_tools = {"agentcad_agentcad_dhealth": "agentcad.health"}
    provider._tool_handler = lambda name, arguments: {
        "ok": True,
        "tool": name,
        "value": arguments["value"] * 2,
    }
    events: list[AgentEvent] = []
    provider._event_handler = events.append
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_send", sent.append)
    provider._handle_server_request(
        {
            "id": 11,
            "method": "item/tool/call",
            "params": {"tool": "agentcad_agentcad_dhealth", "arguments": {"value": 4}},
        }
    )
    response = sent[0]
    assert response["id"] == 11
    assert response["result"]["success"] is True
    content = response["result"]["contentItems"]
    assert content[0]["type"] == "inputText"
    assert json.loads(content[0]["text"]) == {
        "ok": True,
        "tool": "agentcad.health",
        "value": 8,
    }
    assert [event.kind for event in events] == ["tool_started", "tool_completed"]
    assert provider._tool_calls == 1


def test_dynamic_tool_answers_when_advisory_event_handler_fails(monkeypatch) -> None:
    provider = CodexProvider()
    provider._transport_tools = {"agentcad_agentcad_dhealth": "agentcad.health"}
    provider._tool_handler = lambda _name, _arguments: {"ok": True}
    provider._event_handler = lambda _event: (_ for _ in ()).throw(
        RuntimeError("panel destroyed")
    )
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_send", sent.append)
    provider._handle_dynamic_tool(
        22,
        {"tool": "agentcad_agentcad_dhealth", "arguments": {}},
    )
    assert sent[0]["result"]["success"] is True
    assert any("event delivery failed" in item for item in provider._stderr)


def test_dynamic_tool_budget_and_missing_handler_return_failure(monkeypatch) -> None:
    provider = CodexProvider()
    provider._max_tool_calls = 0
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_send", sent.append)
    provider._handle_dynamic_tool(12, {"tool": "agentcad.health", "arguments": []})
    response = sent[0]["result"]
    assert response["success"] is False
    payload = json.loads(response["contentItems"][0]["text"])
    assert "budget exceeded" in payload["error"]


def test_wait_for_turn_streams_text_and_reports_completion() -> None:
    provider = CodexProvider(turn_timeout=1.0)
    provider._notifications.put(
        {
            "method": "item/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "reasoning"},
            },
        }
    )
    provider._notifications.put(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "Created "},
        }
    )
    provider._notifications.put(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "the plate."},
        }
    )
    provider._notifications.put(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "turn": {"status": "completed"},
            },
        }
    )
    events: list[AgentEvent] = []
    result = provider._wait_for_turn(
        thread_id="thread-1",
        turn_id="turn-1",
        cancel_event=threading.Event(),
        on_event=events.append,
        schema_revision="schema-1",
    )
    assert result.text == "Created the plate."
    assert result.thread_id == "thread-1"
    assert result.turn_id == "turn-1"
    assert result.metadata == {"schema_revision": "schema-1"}
    assert [event.kind for event in events] == ["activity", "text_delta", "text_delta"]


def test_wait_for_turn_uses_final_item_and_surfaces_failure() -> None:
    provider = CodexProvider(turn_timeout=1.0)
    provider._notifications.put(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "turn": {
                    "status": "completed",
                    "items": [{"type": "agentMessage", "text": "Final response"}],
                },
            },
        }
    )
    result = provider._wait_for_turn(
        thread_id="thread-1",
        turn_id="turn-1",
        cancel_event=threading.Event(),
        on_event=lambda _event: None,
        schema_revision="schema",
    )
    assert result.text == "Final response"

    provider._notifications.put(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-2",
                "turnId": "turn-2",
                "turn": {"status": "failed", "error": {"message": "model unavailable"}},
            },
        }
    )
    with pytest.raises(CodexProtocolError, match="model unavailable"):
        provider._wait_for_turn(
            thread_id="thread-2",
            turn_id="turn-2",
            cancel_event=threading.Event(),
            on_event=lambda _event: None,
            schema_revision="schema",
        )


def test_wait_for_turn_prefers_structured_final_messages_over_stream_fragments() -> None:
    provider = CodexProvider(turn_timeout=1.0)
    provider._notifications.put(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "message-1",
                "delta": "Inspecting.",
            },
        }
    )
    provider._notifications.put(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "message-2",
                "delta": "Created the feature.",
            },
        }
    )
    provider._notifications.put(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "turn": {
                    "status": "completed",
                    "items": [
                        {"type": "agentMessage", "text": "Inspecting."},
                        {"type": "agentMessage", "text": "Created the feature."},
                    ],
                },
            },
        }
    )
    result = provider._wait_for_turn(
        thread_id="thread-1",
        turn_id="turn-1",
        cancel_event=threading.Event(),
        on_event=lambda _event: None,
        schema_revision="schema",
    )
    assert result.text == "Inspecting.\nCreated the feature."


def test_wait_for_turn_uses_authoritative_completed_item_without_deltas() -> None:
    provider = CodexProvider(turn_timeout=1.0)
    provider._notifications.put(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "id": "message-1",
                    "type": "agentMessage",
                    "text": "Completed from the authoritative item.",
                },
            },
        }
    )
    provider._notifications.put(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "turn": {"status": "completed", "items": []},
            },
        }
    )
    result = provider._wait_for_turn(
        thread_id="thread-1",
        turn_id="turn-1",
        cancel_event=threading.Event(),
        on_event=lambda _event: None,
        schema_revision="schema",
    )
    assert result.text == "Completed from the authoritative item."


def test_semantic_version_parser_preserves_prerelease_state() -> None:
    assert _parse_codex_version("codex-cli 0.138.0-alpha.1") == (
        0,
        138,
        0,
        "alpha.1",
    )
    assert _parse_codex_version("codex-cli 0.145.0") == (0, 145, 0, None)


def test_timeout_interrupts_and_closes_before_returning(monkeypatch) -> None:
    provider = CodexProvider(turn_timeout=0.0)
    stopped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        provider,
        "_interrupt_and_close",
        lambda thread_id, turn_id: stopped.append((thread_id, turn_id)),
    )
    with pytest.raises(TimeoutError, match="turn exceeded"):
        provider._wait_for_turn(
            thread_id="thread-timeout",
            turn_id="turn-timeout",
            cancel_event=threading.Event(),
            on_event=lambda _event: None,
            schema_revision="schema",
        )
    assert stopped == [("thread-timeout", "turn-timeout")]


@pytest.mark.parametrize("item_type", ["commandExecution", "collabToolCall", "imageView"])
def test_started_builtin_capability_interrupts_turn(monkeypatch, item_type: str) -> None:
    provider = CodexProvider(turn_timeout=1.0)
    provider._notifications.put(
        {
            "method": "item/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": item_type},
            },
        }
    )
    stopped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        provider,
        "_interrupt_and_close",
        lambda thread_id, turn_id: stopped.append((thread_id, turn_id)),
    )
    events: list[AgentEvent] = []
    with pytest.raises(CodexProtocolError, match="unsupported capability"):
        provider._wait_for_turn(
            thread_id="thread-1",
            turn_id="turn-1",
            cancel_event=threading.Event(),
            on_event=events.append,
            schema_revision="schema",
        )
    assert stopped == [("thread-1", "turn-1")]
    assert events[0].kind == "provider_warning"


def test_turn_prompt_and_display_helpers_are_bounded_and_stable() -> None:
    recent = [
        {
            "role": "user",
            "content": f"request-{index}",
            "metadata": {"turn_owner": {"host": "private-workstation"}},
        }
        for index in range(8)
    ] + [
        {"role": "system", "content": "excluded"}
    ]
    prompt = _turn_prompt(
        "Make a bracket",
        {"document": {"object_count": 2}},
        {"revision": "memory-1", "entries": []},
        recent,
    )
    assert "USER REQUEST\nMake a bracket" in prompt
    assert "LIVE FREECAD STATE" in prompt
    assert "DURABLE PROJECT MEMORY" in prompt
    assert "excluded" not in prompt
    assert "private-workstation" not in prompt
    assert "request-0" not in prompt
    assert "request-7" in prompt
    assert (
        _final_text({"items": [{"type": "agentMessage", "text": "one"}, {"type": "reasoning"}]})
        == "one"
    )
    assert _item_label({"type": "dynamicToolCall"}) == "Using an Agent Workbench tool"
    assert _item_label({"type": "custom"}) == "custom"


def test_close_terminates_process_and_is_idempotent() -> None:
    provider = CodexProvider()
    stdin = CallbackStdin(provider)
    process = RunningProcess(stdin)
    provider._process = process
    provider.close()
    assert stdin.closed is True
    assert process.terminated is True
    assert provider._process is None
    provider.close()
