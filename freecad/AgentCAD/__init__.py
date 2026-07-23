"""Agent Workbench for FreeCAD addon package."""

from __future__ import annotations

__version__ = "0.1.0"
PRODUCT_NAME = "Agent Workbench for FreeCAD"
SHORT_NAME = "Agent Workbench"
MANIFEST_NAME = "AgentWorkbench"
LEGACY_ID = "AgentCAD"


def initialize(*, gui: bool = False) -> None:
    """Perform the intentionally small non-GUI addon initialization."""
    try:
        import FreeCAD as App

        App.Console.PrintLog(f"{PRODUCT_NAME} {__version__} loaded (gui={gui})\n")
    except Exception:
        return
