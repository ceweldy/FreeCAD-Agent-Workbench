"""Document and viewport tools."""

from __future__ import annotations

from typing import Any

from ..core.context import ContextCollector
from ..core.tools import SafetyLevel, ToolContext, ToolRegistry, ToolSpec, success

EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="core.get_document_state",
            description=(
                "Read the active FreeCAD document, objects, workbench, selection, "
                "and shape facts."
            ),
            input_schema=EMPTY_SCHEMA,
            safety=SafetyLevel.READ,
            handler=_document_state,
            requires_document=False,
        )
    )
    registry.register(
        ToolSpec(
            name="core.get_selection",
            description=(
                "Read the user's current FreeCAD object and subelement selection "
                "without changing it."
            ),
            input_schema=EMPTY_SCHEMA,
            safety=SafetyLevel.READ,
            handler=_selection,
            requires_document=False,
        )
    )
    registry.register(
        ToolSpec(
            name="core.set_view",
            description=(
                "Set a standard FreeCAD camera direction and optionally fit all "
                "visible objects."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": [
                            "axonometric",
                            "front",
                            "rear",
                            "left",
                            "right",
                            "top",
                            "bottom",
                        ],
                        "description": "Standard FreeCAD camera direction.",
                    },
                    "fit": {
                        "type": "boolean",
                        "description": (
                            "Whether to fit visible geometry after changing the camera."
                        ),
                    },
                },
                "required": ["direction"],
                "additionalProperties": False,
            },
            safety=SafetyLevel.VIEW,
            handler=_set_view,
            requires_document=True,
        )
    )


def _document_state(context: ToolContext, _arguments: dict[str, Any]) -> dict[str, Any]:
    return success(
        state=ContextCollector(
            context.app,
            context.gui,
            document=context.document,
        ).collect()
    )


def _selection(context: ToolContext, _arguments: dict[str, Any]) -> dict[str, Any]:
    result = []
    if context.gui is not None:
        try:
            for item in context.gui.Selection.getSelectionEx():
                result.append(
                    {
                        "object": str(getattr(item, "ObjectName", "")),
                        "subelements": list(getattr(item, "SubElementNames", []) or []),
                    }
                )
        except Exception:
            result = []
    return success(selection=result)


def _set_view(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if context.gui is None:
        raise RuntimeError("FreeCAD GUI is unavailable")
    view = context.gui.activeDocument().activeView()
    method_name = {
        "axonometric": "viewAxonometric",
        "front": "viewFront",
        "rear": "viewRear",
        "left": "viewLeft",
        "right": "viewRight",
        "top": "viewTop",
        "bottom": "viewBottom",
    }[arguments["direction"]]
    getattr(view, method_name)()
    if arguments.get("fit", True):
        view.fitAll()
    return success(direction=arguments["direction"], fitted=arguments.get("fit", True))
