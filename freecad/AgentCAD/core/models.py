"""Shared value objects for Agent Workbench sessions and providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    available: bool
    authenticated: bool
    message: str
    version: str | None = None

    @property
    def ready(self) -> bool:
        return self.available and self.authenticated


@dataclass(frozen=True)
class ProviderTurnResult:
    text: str
    thread_id: str
    turn_id: str
    status: str
    tool_calls: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
