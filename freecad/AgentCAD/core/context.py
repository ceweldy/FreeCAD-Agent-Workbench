"""Compact, provider-safe FreeCAD state collection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ContextCollector:
    def __init__(self, app: Any, gui: Any | None, document: Any | None = None) -> None:
        self.app = app
        self.gui = gui
        self.document = document

    def collect(self) -> dict[str, Any]:
        document = self.document or getattr(self.app, "ActiveDocument", None)
        all_objects = []
        if document is not None:
            all_objects = list(getattr(document, "Objects", []))
        objects = [self._object(item) for item in all_objects[:150]]
        selection = []
        if self.gui is not None:
            try:
                selection = [
                    str(item.ObjectName) for item in self.gui.Selection.getSelectionEx()
                ]
            except Exception:
                selection = []
        file_path = str(getattr(document, "FileName", "")) if document else ""
        state = {
            "document": {
                "name": str(getattr(document, "Name", "")) if document else None,
                "label": str(getattr(document, "Label", "")) if document else None,
                "file_name": Path(file_path).name if file_path else None,
                "object_count": len(all_objects),
                "returned_object_count": len(objects),
                "objects_truncated": len(objects) < len(all_objects),
                "objects": objects,
            },
            "selection": selection,
            "workbench": self._workbench(),
            "units": "FreeCAD document units; lengths in millimeters unless reported otherwise",
        }
        canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
        state["revision"] = hashlib.sha256(canonical.encode()).hexdigest()
        return state

    def _workbench(self) -> str | None:
        if self.gui is None:
            return None
        try:
            workbench = self.gui.activeWorkbench()
            return str(workbench.name())
        except Exception:
            return None

    @staticmethod
    def _object(item: Any) -> dict[str, Any]:
        shape = getattr(item, "Shape", None)
        payload = {
            "name": str(getattr(item, "Name", "")),
            "label": str(getattr(item, "Label", "")),
            "type": str(getattr(item, "TypeId", "")),
        }
        if shape is not None and not _is_null_shape(shape):
            payload["shape"] = {
                "valid": _shape_valid(shape),
                "solids": len(getattr(shape, "Solids", []) or []),
                "faces": len(getattr(shape, "Faces", []) or []),
                "edges": len(getattr(shape, "Edges", []) or []),
                "volume": round(float(getattr(shape, "Volume", 0.0) or 0.0), 6),
                "bounds": _bounds(getattr(shape, "BoundBox", None)),
            }
        return payload


def _is_null_shape(shape: Any) -> bool:
    try:
        return bool(shape.isNull())
    except Exception:
        return False


def _shape_valid(shape: Any) -> bool:
    try:
        return bool(shape.isValid())
    except Exception:
        return True


def _bounds(bound_box: Any) -> dict[str, float] | None:
    if bound_box is None:
        return None
    names = ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
    try:
        return {name: round(float(getattr(bound_box, name)), 6) for name in names}
    except Exception:
        return None
