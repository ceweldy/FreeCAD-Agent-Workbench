"""OpenAI Codex app-server provider using the official JSONL protocol."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..core.models import AgentEvent, ProviderStatus, ProviderTurnResult
from ..core.tools import ToolSpec
from .base import AgentProvider, EventHandler, ToolHandler

SYSTEM_INSTRUCTIONS = """You are AgentCAD, an expert CAD operator embedded in FreeCAD.
Work only through the dynamic AgentCAD tools supplied by the host. Do not run shell
commands, inspect the filesystem, edit files, use web search, or request broader
permissions. Any built-in command or non-AgentCAD tool attempt will terminate the turn.
Inspect the live document before making assumptions. Treat all lengths as
millimeters unless the live state says otherwise. Honor durable project memory as the
user's established requirements. Use memory.remember for durable requirements,
constraints, preferences, decisions, and unresolved design questions. Before every CAD
write, verify the intended target and dimensions. After every CAD write, inspect its
receipt and repair or explain any failed postcondition. Never delete an object or export
data unless the host explicitly exposes an approval-gated tool. End with a concise
summary of what changed, verification performed, and any question that still blocks a
correct design."""

CODEX_PERMISSION_PROFILE = "agentcad_runtime"
MINIMUM_CODEX_VERSION = (0, 138, 0)
CODEX_CONFIG_OVERRIDES = (
    f'default_permissions="{CODEX_PERMISSION_PROFILE}"',
    (
        f"permissions.{CODEX_PERMISSION_PROFILE}.description="
        '"AgentCAD isolated read-only runtime"'
    ),
    (
        f"permissions.{CODEX_PERMISSION_PROFILE}.filesystem="
        '{":minimal"="read",":workspace_roots"={"."="read"}}'
    ),
    f"permissions.{CODEX_PERMISSION_PROFILE}.network.enabled=false",
    'web_search="disabled"',
    'shell_environment_policy.inherit="core"',
    "shell_environment_policy.ignore_default_excludes=false",
    "features.apps=false",
    "features.plugins=false",
    "features.browser_use=false",
    "features.computer_use=false",
    "features.image_generation=false",
    "features.goals=false",
    "features.hooks=false",
    "features.memories=false",
    "features.multi_agent=false",
    "features.remote_plugin=false",
    "features.shell_snapshot=false",
    "features.shell_tool=false",
    "features.skill_mcp_dependency_install=false",
    "tools.view_image=false",
    'cli_auth_credentials_store="file"',
)

ALLOWED_CODEX_ITEM_TYPES = {
    "agentMessage",
    "contextCompaction",
    "dynamicToolCall",
    "enteredReviewMode",
    "exitedReviewMode",
    "plan",
    "reasoning",
    "userMessage",
}


class CodexProtocolError(RuntimeError):
    pass


class CodexProvider(AgentProvider):
    provider_id = "codex"
    display_name = "OpenAI Codex"

    def __init__(
        self,
        executable: str | None = None,
        *,
        model: str | None = None,
        reasoning_effort: str = "high",
        request_timeout: float = 30.0,
        turn_timeout: float = 900.0,
        state_dir: str | Path | None = None,
        process_factory: Any = subprocess.Popen,
    ) -> None:
        self.executable_override = executable
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.request_timeout = request_timeout
        self.turn_timeout = turn_timeout
        self.state_dir = (
            Path(state_dir).expanduser()
            if state_dir is not None
            else _default_agentcad_codex_home()
        )
        self.process_factory = process_factory
        self._process: Any | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr: deque[str] = deque(maxlen=30)
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._turn_lock = threading.Lock()
        self._next_id = 1
        self._tool_handler: ToolHandler | None = None
        self._event_handler: EventHandler | None = None
        self._transport_tools: dict[str, str] = {}
        self._tool_calls = 0
        self._max_tool_calls = 40
        self._closed = False

    def executable(self) -> str | None:
        if self.executable_override:
            path = Path(self.executable_override).expanduser()
            return str(path) if path.exists() else None
        from_env = os.environ.get("AGENTCAD_CODEX_BIN")
        if from_env:
            path = Path(from_env).expanduser()
            return str(path) if path.exists() else None
        return shutil.which("codex")

    def status(self) -> ProviderStatus:
        executable = self.executable()
        if not executable:
            return ProviderStatus(
                self.provider_id,
                available=False,
                authenticated=False,
                message="Codex CLI was not found. Install Codex, then run codex login.",
            )
        version = _run_status([executable, "--version"])
        version_text = version[1].strip().splitlines()[0] if version[0] and version[1] else None
        parsed_version = _parse_codex_version(version_text or "")
        unsupported_version = parsed_version is None or (
            parsed_version[:3] < MINIMUM_CODEX_VERSION
            or (
                parsed_version[:3] == MINIMUM_CODEX_VERSION
                and parsed_version[3] is not None
            )
        )
        if unsupported_version:
            required = ".".join(str(item) for item in MINIMUM_CODEX_VERSION)
            return ProviderStatus(
                self.provider_id,
                available=True,
                authenticated=False,
                message=f"AgentCAD requires Codex CLI {required} or newer.",
                version=version_text,
            )
        try:
            isolated_home = self._isolated_home()
        except RuntimeError as exc:
            return ProviderStatus(
                self.provider_id,
                available=True,
                authenticated=False,
                message=str(exc),
                version=version_text,
            )
        login = _run_status(
            [
                executable,
                "login",
                "status",
                "-c",
                'cli_auth_credentials_store="file"',
            ],
            env=_sanitized_environment(isolated_home),
        )
        login_message = login[1].lower()
        negative_markers = ("not logged in", "not authenticated", "logged out")
        positive_markers = ("logged in", "authenticated", "chatgpt", "api key")
        authenticated = (
            login[0]
            and not any(marker in login_message for marker in negative_markers)
            and any(marker in login_message for marker in positive_markers)
        )
        message = (
            "Codex is connected."
            if authenticated
            else (
                "Codex is installed but AgentCAD's isolated Codex home is not "
                "connected. Choose Connect in the AgentCAD panel."
            )
        )
        return ProviderStatus(
            self.provider_id,
            available=True,
            authenticated=authenticated,
            message=message,
            version=version_text,
        )

    def run_turn(
        self,
        *,
        prompt: str,
        context: dict[str, Any],
        memory: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        tools: list[ToolSpec],
        schema_revision: str,
        runtime_dir: str,
        existing_thread_id: str | None,
        tool_handler: ToolHandler,
        on_event: EventHandler,
        cancel_event: threading.Event,
    ) -> ProviderTurnResult:
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError("Codex is already running an AgentCAD turn")
        try:
            return self._run_turn_locked(
                prompt=prompt,
                context=context,
                memory=memory,
                recent_turns=recent_turns,
                tools=tools,
                schema_revision=schema_revision,
                runtime_dir=runtime_dir,
                existing_thread_id=existing_thread_id,
                tool_handler=tool_handler,
                on_event=on_event,
                cancel_event=cancel_event,
            )
        finally:
            self._turn_lock.release()

    def login_chatgpt(
        self,
        *,
        on_challenge: Any,
        cancel_event: threading.Event,
        timeout: float = 300.0,
    ) -> ProviderStatus:
        """Authenticate the dedicated AgentCAD Codex home through app-server."""
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError("Codex is already handling an AgentCAD activity")
        try:
            self._ensure_started()
            account = self._request(
                "account/read",
                {"refreshToken": False},
                timeout=self.request_timeout,
            )
            if account.get("account"):
                return self.status()
            self._drain_notifications()
            response = self._request(
                "account/login/start",
                {"type": "chatgpt"},
                timeout=self.request_timeout,
            )
            login_id = str(response.get("loginId") or "")
            auth_url = str(response.get("authUrl") or "")
            if not login_id or not auth_url.startswith("https://"):
                raise CodexProtocolError(
                    "Codex did not return a valid ChatGPT login challenge"
                )
            on_challenge({"login_id": login_id, "auth_url": auth_url})
            return self._wait_for_login(
                login_id=login_id,
                cancel_event=cancel_event,
                timeout=timeout,
            )
        finally:
            self._turn_lock.release()

    def _wait_for_login(
        self,
        *,
        login_id: str,
        cancel_event: threading.Event,
        timeout: float,
    ) -> ProviderStatus:
        deadline = time.monotonic() + timeout
        cancelled = False
        while time.monotonic() < deadline:
            if cancel_event.is_set() and not cancelled:
                cancelled = True
                with suppress(Exception):
                    self._request(
                        "account/login/cancel",
                        {"loginId": login_id},
                        timeout=min(10.0, self.request_timeout),
                    )
            try:
                message = self._notifications.get(timeout=0.1)
            except queue.Empty:
                self._check_process()
                continue
            if message.get("method") != "account/login/completed":
                continue
            params = message.get("params") or {}
            if str(params.get("loginId") or "") != login_id:
                continue
            if not params.get("success"):
                error = str(params.get("error") or "ChatGPT login was cancelled")
                raise CodexProtocolError(error)
            account = self._request(
                "account/read",
                {"refreshToken": False},
                timeout=self.request_timeout,
            )
            if not account.get("account"):
                raise CodexProtocolError(
                    "ChatGPT login completed, but the AgentCAD Codex home is not ready"
                )
            return ProviderStatus(
                self.provider_id,
                available=True,
                authenticated=True,
                message="Codex is connected.",
            )
        with suppress(Exception):
            self._request(
                "account/login/cancel",
                {"loginId": login_id},
                timeout=min(10.0, self.request_timeout),
            )
        raise TimeoutError("ChatGPT login timed out")

    def _run_turn_locked(
        self,
        *,
        prompt: str,
        context: dict[str, Any],
        memory: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        tools: list[ToolSpec],
        schema_revision: str,
        runtime_dir: str,
        existing_thread_id: str | None,
        tool_handler: ToolHandler,
        on_event: EventHandler,
        cancel_event: threading.Event,
    ) -> ProviderTurnResult:
        if not str(prompt).strip():
            raise ValueError("AgentCAD prompts cannot be empty")
        status = self.status()
        if not status.ready:
            raise RuntimeError(status.message)
        self._ensure_started()
        self._tool_handler = tool_handler
        self._event_handler = on_event
        self._transport_tools = {spec.transport_name: spec.name for spec in tools}
        self._tool_calls = 0
        self._drain_notifications()
        thread_id = ""
        turn_id = ""
        turn_requested = False
        try:
            runtime_path = Path(runtime_dir).expanduser().resolve()
            runtime_path.mkdir(parents=True, exist_ok=True)
            thread_id = self._resume_or_start_thread(
                existing_thread_id=existing_thread_id,
                runtime_dir=runtime_path,
                tools=tools,
            )
            turn_input = _turn_prompt(prompt, context, memory, recent_turns)
            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": turn_input, "text_elements": []}],
                "cwd": str(runtime_path),
                "effort": self.reasoning_effort,
                "permissions": CODEX_PERMISSION_PROFILE,
                "approvalPolicy": "never",
                "runtimeWorkspaceRoots": [str(runtime_path)],
            }
            if self.model:
                turn_params["model"] = self.model
            turn_requested = True
            response = self._request(
                "turn/start", turn_params, timeout=self.request_timeout
            )
            turn = response.get("turn") or {}
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                raise CodexProtocolError("Codex turn/start did not return a turn id")
            self._emit_event(
                on_event,
                AgentEvent("turn_started", "Codex is working", {"turn_id": turn_id})
            )
            return self._wait_for_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                cancel_event=cancel_event,
                on_event=on_event,
                schema_revision=schema_revision,
            )
        except BaseException:
            if turn_id:
                self._interrupt_and_close(thread_id, turn_id)
            elif turn_requested:
                self.close()
            raise
        finally:
            self._tool_handler = None
            self._event_handler = None
            self._transport_tools = {}

    def _resume_or_start_thread(
        self,
        *,
        existing_thread_id: str | None,
        runtime_dir: Path,
        tools: list[ToolSpec],
    ) -> str:
        common = {
            "cwd": str(runtime_dir),
            "developerInstructions": SYSTEM_INSTRUCTIONS,
            "permissions": CODEX_PERMISSION_PROFILE,
            "approvalPolicy": "never",
            "runtimeWorkspaceRoots": [str(runtime_dir)],
        }
        if self.model:
            common["model"] = self.model
        if existing_thread_id:
            try:
                result = self._request(
                    "thread/resume",
                    {"threadId": existing_thread_id, **common},
                    timeout=self.request_timeout,
                )
                thread = result.get("thread") or {}
                resumed = str(thread.get("id") or existing_thread_id)
                if resumed:
                    return resumed
            except CodexProtocolError:
                pass
        params = {
            **common,
            "dynamicTools": [spec.dynamic_tool() for spec in tools],
            "ephemeral": False,
        }
        result = self._request("thread/start", params, timeout=self.request_timeout)
        thread = result.get("thread") or {}
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise CodexProtocolError("Codex thread/start did not return a thread id")
        return thread_id

    def _wait_for_turn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        cancel_event: threading.Event,
        on_event: EventHandler,
        schema_revision: str,
    ) -> ProviderTurnResult:
        deadline = time.monotonic() + self.turn_timeout
        streamed: dict[str, list[str]] = {}
        message_order: list[str] = []
        completed_messages: list[str] = []
        active_message_id = ""
        interrupted = False
        while time.monotonic() < deadline:
            if cancel_event.is_set() and not interrupted:
                interrupted = True
                with suppress(Exception):
                    self._request(
                        "turn/interrupt",
                        {"threadId": thread_id, "turnId": turn_id},
                        timeout=10.0,
                    )
                self._emit_event(
                    on_event,
                    AgentEvent("turn_stopping", "Stopping after the current step"),
                )
            try:
                message = self._notifications.get(timeout=0.1)
            except queue.Empty:
                self._check_process()
                continue
            method = str(message.get("method") or "")
            params = message.get("params") or {}
            if params.get("threadId") not in (None, thread_id):
                continue
            if params.get("turnId") not in (None, turn_id):
                continue
            if method == "item/agentMessage/delta":
                delta = str(params.get("delta") or "")
                if delta:
                    item_id = str(params.get("itemId") or active_message_id or "message")
                    if item_id not in streamed:
                        streamed[item_id] = []
                        message_order.append(item_id)
                    streamed[item_id].append(delta)
                    self._emit_event(on_event, AgentEvent("text_delta", delta))
            elif method == "item/started":
                item = params.get("item") or {}
                item_type = str(item.get("type") or "")
                if item_type == "agentMessage":
                    active_message_id = str(
                        item.get("id") or params.get("itemId") or "message"
                    )
                if item_type not in ALLOWED_CODEX_ITEM_TYPES:
                    self._emit_event(
                        on_event,
                        AgentEvent(
                            "provider_warning",
                            f"Blocked unsupported Codex capability: {item_type}",
                        )
                    )
                    self._interrupt_and_close(thread_id, turn_id)
                    raise CodexProtocolError(
                        f"Codex attempted unsupported capability: {item_type}"
                    )
                self._emit_event(
                    on_event,
                    AgentEvent(
                        "activity", _item_label(item), {"item": item.get("type")}
                    ),
                )
            elif method == "item/completed":
                item = params.get("item") or {}
                item_type = str(item.get("type") or "")
                if item_type not in ALLOWED_CODEX_ITEM_TYPES:
                    self._emit_event(
                        on_event,
                        AgentEvent(
                            "provider_warning",
                            f"Blocked unsupported Codex capability: {item_type}",
                        ),
                    )
                    self._interrupt_and_close(thread_id, turn_id)
                    raise CodexProtocolError(
                        f"Codex attempted unsupported capability: {item_type}"
                    )
                if item.get("type") == "agentMessage":
                    text = str(item.get("text") or "").strip()
                    if text:
                        completed_messages.append(text)
            elif method == "error":
                self._emit_event(
                    on_event,
                    AgentEvent("provider_warning", str(params.get("message") or "Codex error"))
                )
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                status = str(turn.get("status") or "completed")
                fallback = (
                    "".join(streamed[message_order[-1]]).strip() if message_order else ""
                )
                text = _final_text(turn) or "\n".join(completed_messages) or fallback
                if status == "failed":
                    error = turn.get("error") or {}
                    raise CodexProtocolError(str(error.get("message") or "Codex turn failed"))
                return ProviderTurnResult(
                    text=text or "Codex completed without a text response.",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    status=status,
                    tool_calls=self._tool_calls,
                    metadata={"schema_revision": schema_revision},
                )
        self._interrupt_and_close(thread_id, turn_id)
        raise TimeoutError(f"Codex turn exceeded {self.turn_timeout:.0f} seconds")

    def _interrupt_and_close(self, thread_id: str, turn_id: str) -> None:
        with suppress(Exception):
            self._request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
                timeout=min(10.0, self.request_timeout),
            )
        self.close()

    def _ensure_started(self) -> None:
        with self._state_lock:
            if self._process is not None and self._process.poll() is None:
                return
            executable = self.executable()
            if not executable:
                raise RuntimeError("Codex CLI was not found")
            self._closed = False
            command = [executable, "app-server", "--listen", "stdio://"]
            for override in CODEX_CONFIG_OVERRIDES:
                command.extend(["-c", override])
            isolated_home = self._isolated_home()
            try:
                self._process = self.process_factory(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    env=_sanitized_environment(isolated_home),
                )
            except Exception:
                self.close()
                raise
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="AgentCAD-Codex-reader",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop,
                name="AgentCAD-Codex-stderr",
                daemon=True,
            )
            self._reader_thread.start()
            self._stderr_thread.start()
            try:
                self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "agentcad",
                            "title": "AgentCAD for FreeCAD",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    timeout=self.request_timeout,
                )
                self._notify("initialized", {})
            except Exception:
                self.close()
                raise

    def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                message = json.loads(line)
            except (TypeError, ValueError):
                self._stderr.append(f"Invalid app-server JSON: {line[:300].rstrip()}")
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message and "method" in message:
                self._handle_server_request(message)
            elif "id" in message:
                request_id = int(message["id"])
                pending = self._pending.get(request_id)
                if pending is not None:
                    pending.put(message)
            elif "method" in message:
                self._notifications.put(message)

    def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            clean = line.strip()
            if clean:
                self._stderr.append(clean[:500])

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if method == "item/tool/call":
            self._handle_dynamic_tool(request_id, params)
            return
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            self._send({"id": request_id, "result": {"decision": "decline"}})
            return
        if method == "item/permissions/requestApproval":
            self._send(
                {
                    "id": request_id,
                    "result": {"permissions": {}, "scope": "turn"},
                }
            )
            return
        if method in {"applyPatchApproval", "execCommandApproval"}:
            self._send({"id": request_id, "result": {"decision": "denied"}})
            return
        self._send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"AgentCAD does not permit app-server request {method}",
                },
            }
        )

    def _handle_dynamic_tool(self, request_id: Any, params: dict[str, Any]) -> None:
        transport_name = str(params.get("tool") or "")
        tool_name = self._transport_tools.get(transport_name, transport_name)
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        self._tool_calls += 1
        if self._event_handler:
            self._emit_event(
                self._event_handler,
                AgentEvent("tool_started", tool_name, {"arguments": arguments}),
            )
        try:
            if self._tool_calls > self._max_tool_calls:
                raise RuntimeError("AgentCAD tool-call budget exceeded")
            if self._tool_handler is None:
                raise RuntimeError("AgentCAD tool handler is unavailable")
            payload = self._tool_handler(tool_name, arguments)
            success = bool(payload.get("ok"))
        except Exception as exc:
            payload = {"ok": False, "error": str(exc), "tool": tool_name}
            success = False
        if self._event_handler:
            self._emit_event(
                self._event_handler,
                AgentEvent("tool_completed", tool_name, {"result": payload}),
            )
        self._send(
            {
                "id": request_id,
                "result": {
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": json.dumps(payload, sort_keys=True, default=str),
                        }
                    ],
                    "success": success,
                },
            }
        )

    def _emit_event(self, handler: EventHandler, event: AgentEvent) -> None:
        """Keep advisory UI event delivery outside protocol correctness."""
        try:
            handler(event)
        except Exception as exc:
            self._stderr.append(
                "AgentCAD event delivery failed: " + exc.__class__.__name__
            )

    def _request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        with self._state_lock:
            request_id = self._next_id
            self._next_id += 1
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
        try:
            self._send({"method": method, "id": request_id, "params": params})
            try:
                response = response_queue.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"Codex app-server request timed out: {method}") from exc
            if "error" in response:
                error = response.get("error") or {}
                raise CodexProtocolError(
                    f"{method}: {error.get('message') or 'unknown app-server error'}"
                )
            result = response.get("result")
            return dict(result) if isinstance(result, dict) else {}
        finally:
            self._pending.pop(request_id, None)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise CodexProtocolError("Codex app-server is not running")
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=True)
        with self._send_lock:
            process.stdin.write(encoded + "\n")
            process.stdin.flush()

    def _check_process(self) -> None:
        process = self._process
        if process is not None and process.poll() is not None:
            details = "; ".join(self._stderr) or "no diagnostic output"
            raise CodexProtocolError(f"Codex app-server exited: {details}")

    def _drain_notifications(self) -> None:
        while True:
            try:
                self._notifications.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            process = self._process
            self._process = None
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
                process.terminate()
                process.wait(timeout=3.0)
            except Exception:
                with suppress(Exception):
                    process.kill()

    def _isolated_home(self) -> Path:
        return _prepare_isolated_codex_home(self.state_dir)


def _run_status(
    command: list[str], *, env: dict[str, str] | None = None
) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=12,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = "\n".join(item for item in (completed.stdout, completed.stderr) if item).strip()
    return completed.returncode == 0, output


def _parse_codex_version(
    value: str,
) -> tuple[int, int, int, str | None] | None:
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", value)
    if match is None:
        return None
    major, minor, patch, prerelease = match.groups()
    return int(major), int(minor), int(patch), prerelease


def _sanitized_environment(codex_home: Path) -> dict[str, str]:
    """Build the minimal host environment inherited by Codex app-server."""
    allowed = {
        "ALLUSERSPROFILE",
        "APPDATA",
        "CODEX_HOME",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "LOGNAME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "NO_COLOR",
        "OS",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
        "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["CODEX_HOME"] = str(codex_home)
    return environment


def _default_agentcad_codex_home() -> Path:
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "AgentCAD"
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AgentCAD"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        root /= "agentcad"
    return root / "codex-home"


def _prepare_isolated_codex_home(target: Path) -> Path:
    """Create private persistent Codex state without importing user configuration."""
    target = target.expanduser().resolve()
    source = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    if target == source:
        raise RuntimeError("AgentCAD's isolated Codex home must differ from CODEX_HOME")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(target, 0o700)

    _write_managed_codex_config(target / "config.toml")
    for forbidden in ("AGENTS.md", "rules", "plugins"):
        path = target / forbidden
        if path.exists():
            raise RuntimeError(
                f"AgentCAD refused unexpected Codex extension state: {forbidden}"
            )
    skills = target / "skills"
    if skills.is_dir() and any(path.name != ".system" for path in skills.iterdir()):
        raise RuntimeError("AgentCAD refused unexpected user skills in its Codex home")

    target_auth = target / "auth.json"
    if target_auth.is_symlink():
        target_auth.unlink()
    return target


def _write_managed_codex_config(path: Path) -> None:
    content = "# Managed by AgentCAD. Provider settings are supplied by the host.\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _turn_prompt(
    prompt: str,
    context: dict[str, Any],
    memory: dict[str, Any],
    recent_turns: list[dict[str, Any]],
) -> str:
    history = [
        {"role": turn.get("role"), "content": turn.get("content")}
        for turn in recent_turns[-6:]
        if turn.get("role") in {"user", "assistant"}
    ]
    return "\n\n".join(
        [
            "USER REQUEST\n" + prompt.strip(),
            "LIVE FREECAD STATE\n" + json.dumps(context, separators=(",", ":"), default=str),
            "DURABLE PROJECT MEMORY\n" + json.dumps(memory, separators=(",", ":"), default=str),
            "RECENT AGENTCAD CONVERSATION\n"
            + json.dumps(history, separators=(",", ":"), default=str),
        ]
    )


def _final_text(turn: dict[str, Any]) -> str:
    messages = [
        str(item.get("text") or "")
        for item in turn.get("items", [])
        if isinstance(item, dict) and item.get("type") == "agentMessage"
    ]
    return "\n".join(item for item in messages if item).strip()


def _item_label(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "activity")
    labels = {
        "agentMessage": "Preparing response",
        "reasoning": "Thinking through the CAD operation",
        "dynamicToolCall": "Using an AgentCAD tool",
        "commandExecution": "Codex requested a blocked command",
        "fileChange": "Codex requested a blocked file change",
    }
    return labels.get(item_type, item_type)
