"""Provider interface for Agent Workbench."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from ..core.models import AgentEvent, ProviderStatus, ProviderTurnResult
from ..core.tools import ToolSpec

ToolHandler = Callable[[str, dict[str, Any]], dict[str, Any]]
EventHandler = Callable[[AgentEvent], None]


class AgentProvider(ABC):
    provider_id: str
    display_name: str

    @abstractmethod
    def status(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    def close(self) -> None:
        return
