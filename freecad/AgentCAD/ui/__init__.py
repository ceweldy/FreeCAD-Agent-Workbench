"""Native Qt user interface for Agent Workbench."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .panel import AgentCADDock

__all__ = ["AgentCADDock"]


def __getattr__(name: str):
    if name == "AgentCADDock":
        from .panel import AgentCADDock

        return AgentCADDock
    raise AttributeError(name)
