"""Provider-callable durable project memory tools."""

from __future__ import annotations

from typing import Any

from ..core.memory import CATEGORIES
from ..core.tools import SafetyLevel, ToolContext, ToolRegistry, ToolSpec, success


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="memory.list",
            description=(
                "Read durable design requirements, constraints, preferences, "
                "decisions, and questions."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            safety=SafetyLevel.READ,
            handler=_list,
            requires_document=True,
        )
    )
    registry.register(
        ToolSpec(
            name="memory.remember",
            description=(
                "Store one durable design fact with conversation provenance for "
                "future Agent Workbench turns."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": sorted(CATEGORIES),
                        "description": "The durable design-fact category.",
                    },
                    "statement": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 1000,
                        "description": (
                            "A concise standalone design fact, not mutable CAD progress."
                        ),
                    },
                },
                "required": ["category", "statement"],
                "additionalProperties": False,
            },
            safety=SafetyLevel.SAFE_WRITE,
            handler=_remember,
            requires_document=True,
        )
    )


def _list(context: ToolContext, _arguments: dict[str, Any]) -> dict[str, Any]:
    return success(memory=context.memory.provider_context())


def _remember(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    source_turn_id = context.source_turn_id
    if not source_turn_id:
        raise RuntimeError("The active conversation turn is unavailable")
    entry = context.memory.remember(
        arguments["category"],
        arguments["statement"],
        source_turn_id=source_turn_id,
        authority="agent",
    )
    return success(entry=entry, memory_revision=context.memory.load()["revision"])
