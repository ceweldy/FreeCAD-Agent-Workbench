"""FreeCAD command and workbench registration for Agent Workbench."""

from __future__ import annotations

from pathlib import Path

from . import PRODUCT_NAME, SHORT_NAME

COMMAND_NAME = "AgentCAD_OpenAssistant"
MINIMUM_FREECAD_VERSION = (1, 1, 0)
_initialized = False


def _icon_path() -> str:
    return str(Path(__file__).parent / "Resources" / "Icons" / "AgentWorkbench.svg")


class OpenAgentCADCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _icon_path(),
            "MenuText": f"Open {SHORT_NAME}",
            "ToolTip": f"Open the {SHORT_NAME} assistant panel",
            "Accel": "A, C",
        }

    def IsActive(self) -> bool:
        return True

    def Activated(self) -> None:
        from .application import get_application

        get_application().show_panel()


def initialize_gui() -> None:
    global _initialized
    if _initialized:
        return

    import FreeCADGui as Gui  # type: ignore[import-not-found]

    # isort: split
    import FreeCAD as App  # type: ignore[import-not-found]

    _require_supported_freecad(App)
    _initialized = True

    from . import __version__
    from .ui.qt import QtCore
    from .ui.settings import AgentCADSettings
    from .ui.workspace import (
        STANDARD_TOOLBAR_NAME,
        available_toolbar_groups,
        register_native_aliases,
    )

    try:
        commands = set(Gui.listCommands())
    except Exception:
        commands = set()
    if COMMAND_NAME not in commands:
        Gui.addCommand(COMMAND_NAME, OpenAgentCADCommand())
    register_native_aliases(Gui)

    workbench_class = getattr(Gui, "Workbench", object)

    class AgentCADWorkbench(workbench_class):
        MenuText = SHORT_NAME
        ToolTip = "Agent-based CAD modeling with bounded FreeCAD tools"
        Icon = _icon_path()

        def Initialize(self) -> None:
            # Register both layouts once. Activated applies the user's visibility choice
            # every time so the setting can change without rebuilding the workbench.
            self.appendToolbar(STANDARD_TOOLBAR_NAME, [COMMAND_NAME])
            groups = available_toolbar_groups(set(Gui.listCommands()))
            for title, group_commands in groups:
                self.appendToolbar(title, list(group_commands))
            self.appendMenu(SHORT_NAME, [COMMAND_NAME])

        def Activated(self) -> None:
            from .application import get_application
            from .ui.workspace import configure_workspace

            configure_workspace(
                App,
                Gui,
                enabled=AgentCADSettings(App).snapshot().fusion_workspace,
            )
            get_application().show_panel()

        def Deactivated(self) -> None:
            return

        def GetClassName(self) -> str:
            return "Gui::PythonWorkbench"

    try:
        Gui.addWorkbench(AgentCADWorkbench())
    except Exception as exc:
        App.Console.PrintWarning(f"{SHORT_NAME} registration failed: {exc}\n")

    App.Console.PrintLog(f"{PRODUCT_NAME} {__version__} GUI loaded\n")
    if AgentCADSettings(App).snapshot().auto_open:
        QtCore.QTimer.singleShot(0, _show_panel_safely)


def _require_supported_freecad(app: object) -> None:
    version_method = getattr(app, "Version", None)
    if not callable(version_method):
        raise RuntimeError(f"{SHORT_NAME} could not determine the FreeCAD version")
    raw_version = list(version_method())
    try:
        version = tuple(int(str(item)) for item in raw_version[:3])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{SHORT_NAME} could not determine the FreeCAD version") from exc
    if len(version) != 3 or version < MINIMUM_FREECAD_VERSION:
        raise RuntimeError(f"{SHORT_NAME} requires FreeCAD 1.1 or newer")


def _show_panel_safely() -> None:
    try:
        from .application import get_application

        get_application().show_panel()
    except Exception:
        try:
            import FreeCAD as App  # type: ignore[import-not-found]

            App.Console.PrintError(f"{SHORT_NAME} could not open its panel.\n")
        except Exception:
            return
