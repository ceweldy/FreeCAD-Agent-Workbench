"""Small PySide compatibility layer for the versions shipped by FreeCAD."""

from __future__ import annotations

from PySide import QtCore, QtGui  # type: ignore[import-not-found]

try:
    from PySide import QtWidgets  # type: ignore[import-not-found]
except ImportError:
    QtWidgets = QtGui


Signal = QtCore.Signal
Slot = QtCore.Slot


def enum_value(owner, group_name: str, value_name: str):
    """Return either a scoped Qt 6 enum or its Qt 5 counterpart."""
    group = getattr(owner, group_name, None)
    if group is not None and hasattr(group, value_name):
        return getattr(group, value_name)
    return getattr(owner, value_name)


RIGHT_DOCK_AREA = enum_value(
    QtCore.Qt,
    "DockWidgetArea",
    "RightDockWidgetArea",
)
USER_ROLE = enum_value(QtCore.Qt, "ItemDataRole", "UserRole")
CURSOR_END = enum_value(QtGui.QTextCursor, "MoveOperation", "End")


def dialog_button(name: str):
    return enum_value(QtWidgets.QDialogButtonBox, "StandardButton", name)


def standard_pixmap(name: str):
    return enum_value(QtWidgets.QStyle, "StandardPixmap", name)


def exec_dialog(dialog) -> int:
    method = getattr(dialog, "exec", None) or dialog.exec_
    return int(method())


def open_external_url(value: str) -> bool:
    return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl(value)))
