"""AgentCAD FreeCAD addon package."""

from __future__ import annotations

__version__ = "0.1.0"


def initialize(*, gui: bool = False) -> None:
    """Perform the intentionally small non-GUI addon initialization."""
    try:
        import FreeCAD as App

        App.Console.PrintLog(f"AgentCAD {__version__} loaded (gui={gui})\n")
    except Exception:
        return
