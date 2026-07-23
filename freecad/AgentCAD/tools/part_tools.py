"""Bounded Part workbench modeling and inspection tools."""

from __future__ import annotations

import re
from typing import Any

from .. import SHORT_NAME
from ..core.tools import SafetyLevel, ToolContext, ToolRegistry, ToolSpec, success
from ..core.transactions import FreeCADTransaction, Verification

POSITION_PROPERTIES = {
    "x": {
        "type": "number",
        "description": "X position of the primitive origin in millimeters.",
    },
    "y": {
        "type": "number",
        "description": "Y position of the primitive origin in millimeters.",
    },
    "z": {
        "type": "number",
        "description": "Z position of the primitive origin in millimeters.",
    },
}


def register(registry: ToolRegistry) -> None:
    registry.register(_primitive_spec("box"))
    registry.register(_primitive_spec("cylinder"))
    registry.register(_primitive_spec("sphere"))
    registry.register(
        ToolSpec(
            name="part.boolean",
            description=(
                "Create a new validated Part boolean from two existing shaped "
                "objects without deleting sources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["union", "cut", "intersection"],
                        "description": "Boolean operation applied as left operation right.",
                    },
                    "left": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Internal name of the left object.",
                    },
                    "right": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Internal name of the right object.",
                    },
                    "label": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                        "description": "Human-facing result label.",
                    },
                },
                "required": ["operation", "left", "right", "label"],
                "additionalProperties": False,
            },
            safety=SafetyLevel.SAFE_WRITE,
            handler=_boolean,
            mutates_document=True,
        )
    )
    registry.register(
        ToolSpec(
            name="part.measure",
            description=(
                "Measure one shaped object and report validity, topology, volume, "
                "area, center, and bounds."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "object": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Internal object name to measure.",
                    }
                },
                "required": ["object"],
                "additionalProperties": False,
            },
            safety=SafetyLevel.READ,
            handler=_measure,
        )
    )


def _primitive_spec(kind: str) -> ToolSpec:
    properties: dict[str, Any] = {
        "label": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "Human-facing object label.",
        },
        **POSITION_PROPERTIES,
    }
    if kind == "box":
        properties.update(
            {
                "length": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Box size along X in millimeters.",
                },
                "width": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Box size along Y in millimeters.",
                },
                "height": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Box size along Z in millimeters.",
                },
            }
        )
        required = ["label", "length", "width", "height"]
    elif kind == "cylinder":
        properties.update(
            {
                "radius": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Cylinder radius in millimeters.",
                },
                "height": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Cylinder height in millimeters.",
                },
            }
        )
        required = ["label", "radius", "height"]
    else:
        properties["radius"] = {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Sphere radius in millimeters.",
        }
        required = ["label", "radius"]
    return ToolSpec(
        name=f"part.create_{kind}",
        description=(
            f"Create one parametric FreeCAD Part {kind} with validated positive "
            "dimensions and placement."
        ),
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        safety=SafetyLevel.SAFE_WRITE,
        handler=lambda context, arguments, selected=kind: _create_primitive(
            context, selected, arguments
        ),
        mutates_document=True,
    )


def _create_primitive(
    context: ToolContext, kind: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    doc = _document(context)
    internal_name = _unique_name(doc, arguments["label"])

    def action() -> dict[str, Any]:
        type_id = {"box": "Part::Box", "cylinder": "Part::Cylinder", "sphere": "Part::Sphere"}[
            kind
        ]
        obj = doc.addObject(type_id, internal_name)
        obj.Label = arguments["label"]
        if kind == "box":
            obj.Length = arguments["length"]
            obj.Width = arguments["width"]
            obj.Height = arguments["height"]
        elif kind == "cylinder":
            obj.Radius = arguments["radius"]
            obj.Height = arguments["height"]
        else:
            obj.Radius = arguments["radius"]
        obj.Placement.Base = context.app.Vector(
            arguments.get("x", 0.0),
            arguments.get("y", 0.0),
            arguments.get("z", 0.0),
        )
        return {"object": obj.Name, "label": obj.Label, "type": obj.TypeId}

    def verify(result: dict[str, Any]) -> Verification:
        obj = doc.getObject(result["object"])
        return _verify_shape(obj, expected_solid=True)

    return FreeCADTransaction(
        doc,
        f"{SHORT_NAME}: create {kind}",
        application=context.app,
    ).run(action, verify)


def _boolean(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    doc = _document(context)
    _recompute_document(doc, "Boolean operands could not be recomputed")
    left = _shaped_object(doc, arguments["left"])
    right = _shaped_object(doc, arguments["right"])
    if left is right:
        raise ValueError("Boolean operands must be different objects")
    internal_name = _unique_name(doc, arguments["label"])

    def action() -> dict[str, Any]:
        operation = arguments["operation"]
        if operation == "union":
            shape = left.Shape.fuse(right.Shape)
        elif operation == "cut":
            shape = left.Shape.cut(right.Shape)
        else:
            shape = left.Shape.common(right.Shape)
        obj = doc.addObject("Part::Feature", internal_name)
        obj.Label = arguments["label"]
        obj.Shape = shape
        return {
            "object": obj.Name,
            "label": obj.Label,
            "operation": operation,
            "sources": [left.Name, right.Name],
        }

    def verify(result: dict[str, Any]) -> Verification:
        obj = doc.getObject(result["object"])
        verification = _verify_shape(obj, expected_solid=False)
        if (
            verification.ok
            and arguments["operation"] != "intersection"
            and not obj.Shape.Solids
        ):
            return Verification(False, "Boolean result contains no solid")
        return verification

    return FreeCADTransaction(
        doc,
        f"{SHORT_NAME}: {arguments['operation']}",
        application=context.app,
    ).run(action, verify)


def _measure(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    doc = _document(context)
    _recompute_document(doc, "Object could not be recomputed before measurement")
    obj = _shaped_object(doc, arguments["object"])
    shape = obj.Shape
    bounds = shape.BoundBox
    center = getattr(shape, "CenterOfMass", None)
    return success(
        object=obj.Name,
        label=obj.Label,
        valid=bool(shape.isValid()),
        closed=bool(shape.isClosed()) if hasattr(shape, "isClosed") else None,
        solids=len(shape.Solids),
        faces=len(shape.Faces),
        edges=len(shape.Edges),
        volume=float(shape.Volume),
        area=float(shape.Area),
        center=[float(center.x), float(center.y), float(center.z)] if center else None,
        bounds={
            "min": [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
            "max": [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
            "size": [float(bounds.XLength), float(bounds.YLength), float(bounds.ZLength)],
        },
    )


def _document(context: ToolContext) -> Any:
    doc = context.document or getattr(context.app, "ActiveDocument", None)
    if doc is None:
        raise RuntimeError("No active FreeCAD document")
    return doc


def _recompute_document(doc: Any, failure_message: str) -> None:
    recompute = getattr(doc, "recompute", None)
    if not callable(recompute):
        raise RuntimeError("FreeCAD document does not support recompute")
    if recompute() is False:
        raise RuntimeError(failure_message)


def _shaped_object(doc: Any, name: str) -> Any:
    obj = doc.getObject(name)
    if obj is None:
        raise ValueError(f"FreeCAD object does not exist: {name}")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise ValueError(f"FreeCAD object has no usable shape: {name}")
    return obj


def _verify_shape(obj: Any, *, expected_solid: bool) -> Verification:
    if obj is None:
        return Verification(False, "Created object is missing")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return Verification(False, "Created object has no shape")
    if not shape.isValid():
        return Verification(False, "Created shape is invalid")
    if expected_solid and not shape.Solids:
        return Verification(False, "Created shape is not a solid")
    return Verification(
        True,
        "Shape is valid",
        {
            "solids": len(shape.Solids),
            "faces": len(shape.Faces),
            "volume": float(shape.Volume),
        },
    )


def _unique_name(doc: Any, label: str) -> str:
    base = (
        re.sub(r"[^A-Za-z0-9_]", "_", label).strip("_")
        or "AgentWorkbenchObject"
    )
    if base[0].isdigit():
        base = "Object_" + base
    if hasattr(doc, "getUniqueObjectName"):
        return str(doc.getUniqueObjectName(base))
    candidate = base
    index = 2
    while doc.getObject(candidate) is not None:
        candidate = f"{base}{index}"
        index += 1
    return candidate
