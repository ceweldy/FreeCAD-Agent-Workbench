"""Rollback-first FreeCAD transaction wrapper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import SHORT_NAME


class TransactionFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class Verification:
    ok: bool
    message: str = ""
    details: dict[str, Any] | None = None


class FreeCADTransaction:
    def __init__(self, document: Any, name: str, *, application: Any) -> None:
        if document is None:
            raise TransactionFailure("No active FreeCAD document")
        if application is None:
            raise TransactionFailure("FreeCAD application API is unavailable")
        self.document = document
        self.name = name
        self.application = application

    def run(
        self,
        action: Callable[[], dict[str, Any]],
        verify: Callable[[dict[str, Any]], Verification],
    ) -> dict[str, Any]:
        before = _snapshot(self.document)
        opened = False
        committed = False
        transaction_id: int | None = None
        try:
            _require_undo_enabled(self.document)
            if _has_pending_transaction(self.document):
                raise TransactionFailure(
                    "FreeCAD already has a pending transaction; finish or cancel it first"
                )
            if _active_transaction_id(self.application) is not None:
                raise TransactionFailure(
                    "FreeCAD already has a reserved transaction; finish or cancel it first"
                )
            if not hasattr(self.document, "openTransaction"):
                raise TransactionFailure("FreeCAD document does not support transactions")
            self.document.openTransaction(self.name)
            transaction_id = _active_transaction_id(self.application)
            if transaction_id is None:
                raise TransactionFailure("FreeCAD refused to open the transaction")
            opened = True
            result = action()
            if not _has_pending_transaction(self.document):
                raise TransactionFailure(
                    "FreeCAD transaction did not capture a document change"
                )
            if hasattr(self.document, "recompute"):
                recompute_result = self.document.recompute()
                if recompute_result is False:
                    raise TransactionFailure("FreeCAD recompute reported failure")
            verification = verify(result)
            if not verification.ok:
                raise TransactionFailure(verification.message or "Postcondition failed")
            if not hasattr(self.document, "commitTransaction"):
                raise TransactionFailure("FreeCAD document cannot commit transactions")
            commit_result = self.document.commitTransaction()
            if commit_result is False:
                raise TransactionFailure("FreeCAD refused to commit the transaction")
            if _has_pending_transaction(self.document):
                raise TransactionFailure("FreeCAD transaction remained pending after commit")
            if _active_transaction_id(self.application) is not None:
                raise TransactionFailure("FreeCAD transaction remained reserved after commit")
            committed = True
            after = _snapshot(self.document)
            return {
                "ok": True,
                "transaction": self.name,
                "committed": committed or not opened,
                "rolled_back": False,
                "result": result,
                "verification": {
                    "ok": True,
                    "message": verification.message,
                    "details": verification.details or {},
                },
                "document_delta": _delta(before, after),
            }
        except Exception as exc:
            rolled_back = self._rollback(
                opened=opened,
                committed=committed,
                before=before,
                transaction_id=transaction_id,
            )
            after = _snapshot(self.document)
            return {
                "ok": False,
                "transaction": self.name,
                "committed": committed,
                "rolled_back": rolled_back,
                "error": str(exc),
                "document_delta": _delta(before, after),
            }

    def _rollback(
        self,
        *,
        opened: bool,
        committed: bool,
        before: dict[str, Any],
        transaction_id: int | None,
    ) -> bool:
        try:
            if opened and not committed and hasattr(self.document, "abortTransaction"):
                abort_result = self.document.abortTransaction()
                if abort_result is False:
                    return False
            elif (committed and hasattr(self.document, "undo")) or (
                opened and hasattr(self.document, "undo")
            ):
                undo_result = self.document.undo()
                if undo_result is False:
                    return False
            else:
                return False
            active_id = _active_transaction_id(self.application)
            if (
                opened
                and not committed
                and transaction_id is not None
                and active_id == transaction_id
            ):
                _close_active_transaction(self.application, transaction_id)
            if _has_pending_transaction(self.document):
                return False
            if _active_transaction_id(self.application) is not None:
                return False
            if hasattr(self.document, "recompute"):
                recompute_result = self.document.recompute()
                if recompute_result is False:
                    return False
            return _snapshot(self.document) == before
        except Exception:
            return False


def _require_undo_enabled(document: Any) -> None:
    value = getattr(document, "UndoMode", None)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TransactionFailure("FreeCAD document cannot report its undo mode")
    if value != 1:
        raise TransactionFailure(
            f"FreeCAD undo mode must be enabled before {SHORT_NAME} can edit "
            "the document"
        )


def _has_pending_transaction(document: Any) -> bool:
    if hasattr(document, "HasPendingTransaction"):
        return bool(document.HasPendingTransaction)
    method = getattr(document, "hasPendingTransaction", None)
    if callable(method):
        return bool(method())
    raise TransactionFailure("FreeCAD document cannot report pending transactions")


def _active_transaction_id(application: Any) -> int | None:
    method = getattr(application, "getActiveTransaction", None)
    if not callable(method):
        raise TransactionFailure(
            "FreeCAD application cannot inspect active transactions"
        )
    value = method()
    if value is None:
        return None
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or not isinstance(value[1], int)
        or value[1] <= 0
    ):
        raise TransactionFailure("FreeCAD returned an invalid active transaction")
    return value[1]


def _close_active_transaction(application: Any, transaction_id: int) -> None:
    method = getattr(application, "closeActiveTransaction", None)
    if not callable(method):
        raise TransactionFailure(
            "FreeCAD application cannot close the active transaction"
        )
    result = method(True, transaction_id)
    if result is False:
        raise TransactionFailure("FreeCAD refused to abort the active transaction")


def _snapshot(document: Any) -> dict[str, Any]:
    objects = {}
    for item in list(getattr(document, "Objects", []) or []):
        name = str(getattr(item, "Name", ""))
        if not name:
            continue
        shape = getattr(item, "Shape", None)
        objects[name] = {
            "type": str(getattr(item, "TypeId", "")),
            "label": str(getattr(item, "Label", name)),
            "shape": _shape_signature(shape),
        }
    return {"objects": objects}


def _shape_signature(shape: Any) -> dict[str, Any] | None:
    if shape is None or bool(getattr(shape, "isNull", lambda: True)()):
        return None
    bounds = getattr(shape, "BoundBox", None)
    return {
        "solids": len(getattr(shape, "Solids", []) or []),
        "faces": len(getattr(shape, "Faces", []) or []),
        "edges": len(getattr(shape, "Edges", []) or []),
        "volume": round(float(getattr(shape, "Volume", 0.0) or 0.0), 9),
        "bounds": _bounds(bounds),
    }


def _bounds(bounds: Any) -> dict[str, float] | None:
    if bounds is None:
        return None
    names = ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
    try:
        return {name: round(float(getattr(bounds, name)), 9) for name in names}
    except Exception:
        return None


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    old = before["objects"]
    new = after["objects"]
    return {
        "created": sorted(set(new) - set(old)),
        "deleted": sorted(set(old) - set(new)),
        "changed": sorted(name for name in set(old) & set(new) if old[name] != new[name]),
    }
