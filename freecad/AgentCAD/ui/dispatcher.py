"""Synchronous dispatch from provider threads to FreeCAD's GUI thread."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .qt import QtCore, Signal, Slot


@dataclass
class _DispatchRequest:
    operation: Callable[[], Any]
    complete: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class MainThreadDispatcher(QtCore.QObject):
    """Execute callables on the Qt thread and return their result to the caller."""

    requested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.requested.connect(self._execute)

    def dispatch(self, operation: Callable[[], Any]) -> Any:
        if QtCore.QThread.currentThread() is self.thread():
            return operation()
        request = _DispatchRequest(operation=operation)
        self.requested.emit(request)
        request.complete.wait()
        if request.error is not None:
            raise request.error
        return request.result

    @Slot(object)
    def _execute(self, request: _DispatchRequest) -> None:
        try:
            request.result = request.operation()
        except BaseException as exc:
            request.error = exc
        finally:
            request.complete.set()
