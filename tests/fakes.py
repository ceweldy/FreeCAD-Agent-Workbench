"""Small FreeCAD-shaped fakes shared by unit tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


@dataclass
class FakeBoundBox:
    XMin: float = 0.0
    YMin: float = 0.0
    ZMin: float = 0.0
    XMax: float = 1.0
    YMax: float = 2.0
    ZMax: float = 3.0

    @property
    def XLength(self) -> float:
        return self.XMax - self.XMin

    @property
    def YLength(self) -> float:
        return self.YMax - self.YMin

    @property
    def ZLength(self) -> float:
        return self.ZMax - self.ZMin


class FakeShape:
    def __init__(
        self,
        *,
        null: bool = False,
        valid: bool = True,
        solids: int = 1,
        faces: int = 6,
        edges: int = 12,
        volume: float = 6.0,
        area: float = 22.0,
        bounds: FakeBoundBox | None = None,
    ) -> None:
        self._null = null
        self._valid = valid
        self.Solids = [object() for _ in range(solids)]
        self.Faces = [object() for _ in range(faces)]
        self.Edges = [object() for _ in range(edges)]
        self.Volume = volume
        self.Area = area
        self.BoundBox = bounds or FakeBoundBox()
        self.CenterOfMass = SimpleNamespace(x=0.5, y=1.0, z=1.5)

    def isNull(self) -> bool:
        return self._null

    def isValid(self) -> bool:
        return self._valid

    def isClosed(self) -> bool:
        return bool(self.Solids)

    def fuse(self, _other: FakeShape) -> FakeShape:
        return FakeShape(volume=self.Volume + 1.0)

    def cut(self, _other: FakeShape) -> FakeShape:
        return FakeShape(volume=max(self.Volume - 1.0, 0.1))

    def common(self, _other: FakeShape) -> FakeShape:
        return FakeShape(volume=1.0)


class FakeObject:
    def __init__(
        self,
        name: str,
        *,
        label: str | None = None,
        type_id: str = "Part::Feature",
        shape: FakeShape | None = None,
    ) -> None:
        self.Name = name
        self.Label = label or name
        self.TypeId = type_id
        self.Shape = shape


class FakeDocument:
    def __init__(self, path: str = "/tmp/model.FCStd") -> None:
        self.Name = "Model"
        self.Label = "Model"
        self.FileName = path
        self.Objects: list[FakeObject] = []
        self.opened: list[str] = []
        self.commits = 0
        self.aborts = 0
        self.undos = 0
        self.recomputes = 0
        self.recompute_result = True
        self.recompute_hook: Callable[[], None] | None = None
        self.saves = 0
        self.save_result: bool | None = None
        self.save_copies: list[str] = []
        self.save_copy_result: bool | None = None
        self.UndoMode = 1
        self.pending_transaction = False
        self.active_transaction_id = 0
        self.active_transaction_name: str | None = None
        self.active_transaction_closes: list[tuple[bool, int]] = []
        self._transaction_objects: list[FakeObject] | None = None

    @property
    def HasPendingTransaction(self) -> bool:
        if self.pending_transaction:
            return True
        return bool(
            self.active_transaction_id
            and self._transaction_objects is not None
            and self.Objects != self._transaction_objects
        )

    def openTransaction(self, name: str) -> None:
        self.opened.append(name)
        self._transaction_objects = list(self.Objects)
        self.active_transaction_id = len(self.opened)
        self.active_transaction_name = name

    def commitTransaction(self) -> None:
        self.commits += 1
        self._transaction_objects = None
        self.pending_transaction = False
        self.active_transaction_id = 0
        self.active_transaction_name = None

    def abortTransaction(self) -> None:
        self.aborts += 1
        if not self.HasPendingTransaction:
            return
        if self._transaction_objects is not None:
            self.Objects = self._transaction_objects
        self._transaction_objects = None
        self.pending_transaction = False
        self.active_transaction_id = 0
        self.active_transaction_name = None

    def undo(self) -> None:
        self.undos += 1

    def recompute(self) -> bool:
        self.recomputes += 1
        if self.recompute_hook is not None:
            self.recompute_hook()
        return self.recompute_result

    def save(self) -> bool | None:
        self.saves += 1
        if self.save_result is False:
            return False
        if self.FileName:
            Path(self.FileName).write_bytes(b"fake-freecad-document")
        return self.save_result

    def saveCopy(self, path: str) -> bool | None:
        self.save_copies.append(path)
        if self.save_copy_result is False:
            return False
        Path(path).write_bytes(b"fake-freecad-checkpoint")
        return self.save_copy_result

    def addObject(self, type_id: str, name: str) -> FakeObject:
        obj = FakeObject(name, type_id=type_id, shape=None)
        self.Objects.append(obj)
        return obj

    def getObject(self, name: str) -> FakeObject | None:
        return next((item for item in self.Objects if item.Name == name), None)

    def getUniqueObjectName(self, base: str) -> str:
        if self.getObject(base) is None:
            return base
        index = 2
        while self.getObject(f"{base}{index}") is not None:
            index += 1
        return f"{base}{index}"


class FakeSelection:
    def __init__(self, entries: list[Any] | None = None) -> None:
        self.entries = entries or []

    def getSelectionEx(self) -> list[Any]:
        return self.entries


class FakeGui:
    def __init__(self, selection: list[Any] | None = None) -> None:
        self.Selection = FakeSelection(selection)

    @staticmethod
    def activeWorkbench() -> Any:
        return SimpleNamespace(name=lambda: "Part")


def fake_app(document: FakeDocument | None) -> Any:
    def get_active_transaction() -> tuple[str, int] | None:
        if document is None or document.active_transaction_id <= 0:
            return None
        return (
            document.active_transaction_name or "Transaction",
            document.active_transaction_id,
        )

    def close_active_transaction(abort: bool = False, transaction_id: int = 0) -> None:
        if document is None:
            return
        document.active_transaction_closes.append((abort, transaction_id))
        if transaction_id not in {0, document.active_transaction_id}:
            return
        if (
            abort
            and document.HasPendingTransaction
            and document._transaction_objects is not None
        ):
            document.Objects = document._transaction_objects
        document._transaction_objects = None
        document.pending_transaction = False
        document.active_transaction_id = 0
        document.active_transaction_name = None

    return SimpleNamespace(
        ActiveDocument=document,
        getActiveTransaction=get_active_transaction,
        closeActiveTransaction=close_active_transaction,
    )
