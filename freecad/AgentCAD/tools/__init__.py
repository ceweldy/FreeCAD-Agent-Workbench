"""Built-in Agent Workbench tool packs."""

from __future__ import annotations

from ..core.tools import ToolRegistry
from . import core_tools, memory_tools, part_tools


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    core_tools.register(registry)
    part_tools.register(registry)
    memory_tools.register(registry)
    return registry
