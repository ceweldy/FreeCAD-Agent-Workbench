"""Familiar solid-modeling workspace built from native FreeCAD commands."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any

from .. import SHORT_NAME

FUSION_NAVIGATION_STYLE = "Gui::RevitNavigationStyle"
WORKBENCH_NAME = "AgentCADWorkbench"
WORKSPACE_TOOLBAR_TITLES = (
    "CREATE",
    "MODIFY",
    "PATTERN",
    "CONSTRUCT",
    "INSPECT",
    "AGENT",
)
STANDARD_TOOLBAR_TITLE = SHORT_NAME
WORKSPACE_TOOLBAR_NAMES = {
    f"AgentCAD_{title}_Toolbar": title for title in WORKSPACE_TOOLBAR_TITLES
}
STANDARD_TOOLBAR_NAME = "AgentCAD_Main_Toolbar"
WORKSPACE_STRIP_NAME = "AgentCAD_WorkspaceStrip_Toolbar"
WORKSPACE_SELECTOR_NAME = "AgentCADWorkspaceSelector"
WORKSPACE_TABS_NAME = "AgentCADWorkspaceTabs"
TIMELINE_DOCK_NAME = "AgentCADTimelineDock"
TIMELINE_WIDGET_NAME = "AgentCADTimeline"
ASSISTANT_DOCK_NAME = "AgentCADDock"
MODEL_DOCK_NAMES = ("Model", "Combo View", "Tree view")
WORKSPACE_CONFIGURED_PROPERTY = "_agentcadFusionWorkspaceConfigured"
TOOLBAR_CONFIGURED_PROPERTY = "_agentcadFusionToolbarConfigured"
DOCK_CONFIGURED_PROPERTY = "_agentcadFusionDockConfigured"
MODEL_DOCK_TITLE_PROPERTY = "_agentcadPreviousDockTitle"
MODEL_DOCK_MIN_WIDTH_PROPERTY = "_agentcadPreviousMinimumWidth"
ASSISTANT_DOCK_MIN_WIDTH_PROPERTY = "_agentcadPreviousAssistantMinimumWidth"

WORKSPACE_CATEGORIES = (
    ("SOLID", ("CREATE", "MODIFY", "PATTERN")),
    ("CONSTRUCT", ("CONSTRUCT",)),
    ("INSPECT", ("INSPECT",)),
    ("AGENT", ("AGENT",)),
)
NAVIGATION_COMMANDS = (
    ("Fit", "Std_ViewFitAll"),
    ("Iso", "Std_ViewIsometric"),
    ("Top", "Std_ViewTop"),
    ("Front", "Std_ViewFront"),
    ("Right", "Std_ViewRight"),
)

TIMELINE_AUXILIARY_NAMES = frozenset(
    {
        "Origin",
        "XAxis",
        "YAxis",
        "ZAxis",
        "XY_Plane",
        "XZ_Plane",
        "YZ_Plane",
    }
)
TIMELINE_AUXILIARY_TYPES = frozenset(
    {
        "App::Line",
        "App::Origin",
        "App::Plane",
    }
)


@dataclass(frozen=True)
class CommandAlias:
    command_id: str
    target_id: str
    label: str
    tooltip: str
    icon: str


@dataclass(frozen=True)
class TimelineEntry:
    object_name: str
    label: str
    type_id: str


COMMAND_ALIASES = (
    CommandAlias(
        "AgentCAD_NewComponent",
        "PartDesign_Body",
        "New Component",
        "Create a FreeCAD Body as the top-level component for a design",
        ":/icons/PartDesign_Body.svg",
    ),
    CommandAlias(
        "AgentCAD_CreateSketch",
        "PartDesign_NewSketch",
        "Create Sketch",
        "Create a constrained sketch on a plane or planar face",
        ":/icons/general/Sketcher_NewSketch.svg",
    ),
    CommandAlias(
        "AgentCAD_ExtrudeAdd",
        "PartDesign_Pad",
        "Extrude (Add)",
        "Add material by extruding the selected sketch, called Pad in FreeCAD",
        ":/icons/PartDesign_Pad.svg",
    ),
    CommandAlias(
        "AgentCAD_Revolve",
        "PartDesign_Revolution",
        "Revolve",
        "Add material by revolving the selected sketch",
        ":/icons/PartDesign_Revolution.svg",
    ),
    CommandAlias(
        "AgentCAD_Sweep",
        "PartDesign_AdditivePipe",
        "Sweep",
        "Sweep a sketch along a path, called Additive Pipe in FreeCAD",
        ":/icons/PartDesign_AdditivePipe.svg",
    ),
    CommandAlias(
        "AgentCAD_Loft",
        "PartDesign_AdditiveLoft",
        "Loft",
        "Create material through two or more sketch profiles",
        ":/icons/PartDesign_AdditiveLoft.svg",
    ),
    CommandAlias(
        "AgentCAD_ExtrudeCut",
        "PartDesign_Pocket",
        "Extrude (Cut)",
        "Remove material by extruding the selected sketch, called Pocket in FreeCAD",
        ":/icons/PartDesign_Pocket.svg",
    ),
    CommandAlias(
        "AgentCAD_Hole",
        "PartDesign_Hole",
        "Hole",
        "Create one or more parametric holes from sketch points or circles",
        ":/icons/PartDesign_Hole.svg",
    ),
    CommandAlias(
        "AgentCAD_Combine",
        "PartDesign_Boolean",
        "Combine",
        "Join, cut, or intersect solid bodies with a Boolean operation",
        ":/icons/PartDesign_Boolean.svg",
    ),
    CommandAlias(
        "AgentCAD_Fillet",
        "PartDesign_Fillet",
        "Fillet",
        "Round selected edges",
        ":/icons/PartDesign_Fillet.svg",
    ),
    CommandAlias(
        "AgentCAD_Chamfer",
        "PartDesign_Chamfer",
        "Chamfer",
        "Bevel selected edges",
        ":/icons/PartDesign_Chamfer.svg",
    ),
    CommandAlias(
        "AgentCAD_Shell",
        "PartDesign_Thickness",
        "Shell",
        "Hollow a solid to a wall thickness, called Thickness in FreeCAD",
        ":/icons/PartDesign_Thickness.svg",
    ),
    CommandAlias(
        "AgentCAD_Draft",
        "PartDesign_Draft",
        "Draft",
        "Apply a draft angle to selected faces",
        ":/icons/PartDesign_Draft.svg",
    ),
    CommandAlias(
        "AgentCAD_Mirror",
        "PartDesign_Mirrored",
        "Mirror",
        "Mirror selected features or the active body",
        ":/icons/PartDesign_Mirrored.svg",
    ),
    CommandAlias(
        "AgentCAD_RectangularPattern",
        "PartDesign_LinearPattern",
        "Rectangular Pattern",
        "Repeat selected features in a linear pattern",
        ":/icons/PartDesign_LinearPattern.svg",
    ),
    CommandAlias(
        "AgentCAD_CircularPattern",
        "PartDesign_PolarPattern",
        "Circular Pattern",
        "Repeat selected features around an axis",
        ":/icons/PartDesign_PolarPattern.svg",
    ),
    CommandAlias(
        "AgentCAD_Measure",
        "Std_Measure",
        "Measure",
        "Measure selected geometry",
        ":/icons/preferences-measure.svg",
    ),
    CommandAlias(
        "AgentCAD_CheckGeometry",
        "Part_CheckGeometry",
        "Check Geometry",
        "Inspect selected shapes for geometry errors",
        ":/icons/booleans/Part_CheckGeometry.svg",
    ),
)

TOOLBAR_GROUPS = (
    (
        "CREATE",
        (
            "AgentCAD_NewComponent",
            "AgentCAD_CreateSketch",
            "AgentCAD_ExtrudeAdd",
            "AgentCAD_Revolve",
            "AgentCAD_Sweep",
            "AgentCAD_Loft",
        ),
    ),
    (
        "MODIFY",
        (
            "AgentCAD_ExtrudeCut",
            "AgentCAD_Hole",
            "AgentCAD_Combine",
            "AgentCAD_Fillet",
            "AgentCAD_Chamfer",
            "AgentCAD_Shell",
            "AgentCAD_Draft",
        ),
    ),
    (
        "PATTERN",
        (
            "AgentCAD_Mirror",
            "AgentCAD_RectangularPattern",
            "AgentCAD_CircularPattern",
        ),
    ),
    ("CONSTRUCT", ("PartDesign_CompDatums",)),
    ("INSPECT", ("AgentCAD_Measure", "AgentCAD_CheckGeometry")),
    ("AGENT", ("AgentCAD_OpenAssistant",)),
)


class NativeCommandAlias:
    """Expose a native command with familiar solid-modeling terminology."""

    def __init__(self, spec: CommandAlias) -> None:
        self.spec = spec

    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": self.spec.icon,
            "MenuText": self.spec.label,
            "ToolTip": self.spec.tooltip,
        }

    def IsActive(self) -> bool:
        try:
            import FreeCADGui as Gui  # type: ignore[import-not-found]

            return bool(Gui.isCommandActive(self.spec.target_id))
        except Exception:
            return False

    def Activated(self) -> None:
        import FreeCADGui as Gui  # type: ignore[import-not-found]

        Gui.runCommand(self.spec.target_id)


def register_native_aliases(gui: Any) -> set[str]:
    """Register aliases whose underlying FreeCAD commands are available."""
    _load_native_command_modules()
    try:
        available = set(gui.listCommands())
    except Exception:
        available = set()
    for spec in COMMAND_ALIASES:
        if spec.target_id not in available or spec.command_id in available:
            continue
        gui.addCommand(spec.command_id, NativeCommandAlias(spec))
        available.add(spec.command_id)
    return available


def available_toolbar_groups(available: set[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    groups = []
    for title, commands in TOOLBAR_GROUPS:
        present = tuple(command for command in commands if command in available)
        if present:
            groups.append((f"AgentCAD_{title}_Toolbar", present))
    return tuple(groups)


def timeline_entries(document: Any) -> tuple[TimelineEntry, ...]:
    """Return user-created document objects in feature-history order."""
    if document is None:
        return ()
    entries = []
    for obj in getattr(document, "Objects", ()):
        object_name = str(getattr(obj, "Name", "")).strip()
        if not object_name or object_name in TIMELINE_AUXILIARY_NAMES:
            continue
        type_id = str(getattr(obj, "TypeId", "")).strip()
        if type_id in TIMELINE_AUXILIARY_TYPES or type_id.startswith("App::Origin"):
            continue
        label = str(getattr(obj, "Label", "")).strip() or object_name
        entries.append(
            TimelineEntry(
                object_name=object_name,
                label=label,
                type_id=type_id,
            )
        )
    return tuple(entries)


def configure_workspace(app: Any, gui: Any, *, enabled: bool, dock: Any = None) -> None:
    if enabled:
        _apply_preferences(app)
        _style_workspace(app, gui, dock=dock)
    else:
        workspace = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")
        was_active = workspace.GetBool("FusionWorkspaceActive", False) or workspace.GetBool(
            "WorkspaceLayoutCaptured", False
        )
        _restore_preferences(app)
        _prepare_docks_for_layout_restore(gui, dock=dock)
        if was_active:
            standard_was_captured = workspace.GetBool(
                "StandardToolbarCaptured",
                False,
            )
            layout_restored = _restore_workspace_layout(app, gui)
            _restore_toolbar_style(
                gui,
                show_standard=not layout_restored or not standard_was_captured,
            )
        else:
            _hide_workspace_toolbars(gui)
        _hide_workspace_chrome(gui)
        _restore_model_dock_title(gui)
        _restore_assistant_dock(gui, dock=dock)


def _apply_preferences(app: Any) -> None:
    workspace = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")
    view = app.ParamGet("User parameter:BaseApp/Preferences/View")
    general = app.ParamGet("User parameter:BaseApp/Preferences/General")

    if not workspace.GetBool("FusionWorkspaceActive", False):
        if not workspace.GetBool("HasPreviousNavigationStyle", False):
            previous_navigation = workspace.GetString("PreviousNavigationStyle", "")
            workspace.SetString(
                "PreviousNavigationStyle",
                previous_navigation
                or view.GetString("NavigationStyle", "Gui::CADNavigationStyle"),
            )
            workspace.SetBool("HasPreviousNavigationStyle", True)
        if not workspace.GetBool("HasPreviousAutoloadModule", False):
            previous_workbench = workspace.GetString("PreviousAutoloadModule", "")
            workspace.SetString(
                "PreviousAutoloadModule",
                previous_workbench
                or general.GetString("AutoloadModule", "$LastModule"),
            )
            workspace.SetBool("HasPreviousAutoloadModule", True)
        if not workspace.GetBool("HasPreviousZoomAtCursor", False):
            workspace.SetBool(
                "PreviousZoomAtCursor",
                view.GetBool("ZoomAtCursor", False),
            )
            workspace.SetBool("HasPreviousZoomAtCursor", True)
        if not workspace.GetBool("HasPreviousShowNaviCube", False):
            workspace.SetBool(
                "PreviousShowNaviCube",
                view.GetBool("ShowNaviCube", True),
            )
            workspace.SetBool("HasPreviousShowNaviCube", True)
        workspace.SetBool("FusionWorkspaceActive", True)

    view.SetString("NavigationStyle", FUSION_NAVIGATION_STYLE)
    view.SetBool("ZoomAtCursor", True)
    view.SetBool("ShowNaviCube", True)
    general.SetString("AutoloadModule", WORKBENCH_NAME)


def _restore_preferences(app: Any) -> None:
    workspace = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")
    if not workspace.GetBool("FusionWorkspaceActive", False):
        return

    view = app.ParamGet("User parameter:BaseApp/Preferences/View")
    general = app.ParamGet("User parameter:BaseApp/Preferences/General")
    previous_navigation = workspace.GetString("PreviousNavigationStyle", "")
    previous_workbench = workspace.GetString("PreviousAutoloadModule", "")
    if workspace.GetBool("HasPreviousNavigationStyle", False):
        view.SetString("NavigationStyle", previous_navigation)
    if workspace.GetBool("HasPreviousAutoloadModule", False):
        general.SetString("AutoloadModule", previous_workbench)
    if workspace.GetBool("HasPreviousZoomAtCursor", False):
        view.SetBool(
            "ZoomAtCursor",
            workspace.GetBool("PreviousZoomAtCursor", False),
        )
    if workspace.GetBool("HasPreviousShowNaviCube", False):
        view.SetBool(
            "ShowNaviCube",
            workspace.GetBool("PreviousShowNaviCube", True),
        )

    for name in ("PreviousNavigationStyle", "PreviousAutoloadModule"):
        workspace.RemString(name)
    for name in (
        "PreviousZoomAtCursor",
        "HasPreviousZoomAtCursor",
        "PreviousShowNaviCube",
        "HasPreviousShowNaviCube",
        "HasPreviousNavigationStyle",
        "HasPreviousAutoloadModule",
        "FusionWorkspaceActive",
    ):
        workspace.RemBool(name)


def _find_model_dock(main_window: Any, dock_type: Any) -> Any:
    for name in MODEL_DOCK_NAMES:
        try:
            dock = main_window.findChild(dock_type, name)
        except Exception:
            return None
        if dock is not None:
            return dock
    return None


def _find_child(main_window: Any, widget_type: Any, object_name: str) -> Any:
    try:
        return main_window.findChild(widget_type, object_name)
    except Exception:
        return None


def _capture_workspace_layout(
    app: Any,
    main_window: Any,
    *,
    standard_toolbar_present: bool = False,
) -> None:
    workspace = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")
    if workspace.GetBool("WorkspaceLayoutCaptured", False):
        return
    encoded = bytes(main_window.saveState().toBase64()).decode("ascii")
    workspace.SetString("PreviousMainWindowState", encoded)
    workspace.SetBool("WorkspaceLayoutCaptured", True)
    workspace.SetBool("StandardToolbarCaptured", standard_toolbar_present)


def _style_workspace(app: Any, gui: Any, *, dock: Any = None) -> None:
    try:
        from .qt import QtCore, QtWidgets, enum_value

        text_style = enum_value(
            QtCore.Qt,
            "ToolButtonStyle",
            "ToolButtonTextUnderIcon",
        )
        main_window = gui.getMainWindow()
        if not bool(main_window.property(WORKSPACE_CONFIGURED_PROPERTY)):
            standard_toolbar_present = any(
                toolbar.objectName() == STANDARD_TOOLBAR_NAME
                for toolbar in main_window.findChildren(QtWidgets.QToolBar)
            )
            _capture_workspace_layout(
                app,
                main_window,
                standard_toolbar_present=standard_toolbar_present,
            )
            left_area = enum_value(QtCore.Qt, "DockWidgetArea", "LeftDockWidgetArea")
            top_area = enum_value(QtCore.Qt, "ToolBarArea", "TopToolBarArea")
            bottom_dock_area = enum_value(
                QtCore.Qt,
                "DockWidgetArea",
                "BottomDockWidgetArea",
            )
            model_dock = _find_model_dock(main_window, QtWidgets.QDockWidget)
            if model_dock is not None:
                if model_dock.property(MODEL_DOCK_TITLE_PROPERTY) is None:
                    model_dock.setProperty(
                        MODEL_DOCK_TITLE_PROPERTY,
                        model_dock.windowTitle(),
                    )
                _capture_minimum_width(model_dock, MODEL_DOCK_MIN_WIDTH_PROPERTY)
                _apply_model_dock_title(model_dock)
                main_window.addDockWidget(left_area, model_dock)
                model_dock.setMinimumWidth(230)
                model_dock.show()
            strip = _ensure_workspace_strip(app, main_window)
            main_window.addToolBar(top_area, strip)
            main_window.addToolBarBreak(top_area)
            for object_name in WORKSPACE_TOOLBAR_NAMES:
                toolbar = _find_child(main_window, QtWidgets.QToolBar, object_name)
                if toolbar is None:
                    continue
                main_window.addToolBar(top_area, toolbar)
            timeline = _ensure_timeline_dock(app, gui, main_window)
            main_window.addDockWidget(bottom_dock_area, timeline)
            main_window.setProperty(WORKSPACE_CONFIGURED_PROPERTY, True)
        for toolbar in main_window.findChildren(QtWidgets.QToolBar):
            object_name = toolbar.objectName()
            display_title = WORKSPACE_TOOLBAR_NAMES.get(object_name)
            if display_title is not None:
                toolbar.setWindowTitle(display_title)
                if bool(toolbar.property(TOOLBAR_CONFIGURED_PROPERTY)):
                    continue
                toolbar.setToolButtonStyle(text_style)
                toolbar.setIconSize(QtCore.QSize(28, 28))
                toolbar.setMovable(False)
                toolbar.setFloatable(False)
                toolbar.setProperty(TOOLBAR_CONFIGURED_PROPERTY, True)
            elif object_name == STANDARD_TOOLBAR_NAME:
                toolbar.setWindowTitle(STANDARD_TOOLBAR_TITLE)
                if bool(toolbar.property(TOOLBAR_CONFIGURED_PROPERTY)):
                    continue
                toolbar.hide()
                toolbar.setProperty(TOOLBAR_CONFIGURED_PROPERTY, True)
        model_dock = _find_model_dock(main_window, QtWidgets.QDockWidget)
        if model_dock is not None:
            _apply_model_dock_title(model_dock)
            single_shot = getattr(getattr(QtCore, "QTimer", None), "singleShot", None)
            if callable(single_shot):
                single_shot(0, partial(_apply_model_dock_title, model_dock))
        _show_active_workspace_category(app, main_window)
        strip = _find_child(main_window, QtWidgets.QToolBar, WORKSPACE_STRIP_NAME)
        if strip is not None:
            strip.show()
        timeline = _find_child(main_window, QtWidgets.QDockWidget, TIMELINE_DOCK_NAME)
        if timeline is not None:
            timeline.show()
            widget = timeline.widget()
            if widget is not None and hasattr(widget, "set_active"):
                widget.set_active(True)
        if dock is not None:
            if not bool(dock.property(DOCK_CONFIGURED_PROPERTY)):
                right_area = enum_value(
                    QtCore.Qt,
                    "DockWidgetArea",
                    "RightDockWidgetArea",
                )
                main_window.addDockWidget(right_area, dock)
                dock.setProperty(DOCK_CONFIGURED_PROPERTY, True)
            _capture_minimum_width(dock, ASSISTANT_DOCK_MIN_WIDTH_PROPERTY)
            dock.setWindowTitle("AGENT WORKBENCH")
            dock.setMinimumWidth(340)
            dock.show()
    except Exception:
        return


def _restore_workspace_layout(app: Any, gui: Any) -> bool:
    workspace = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")
    if not workspace.GetBool("WorkspaceLayoutCaptured", False):
        return False
    encoded = workspace.GetString("PreviousMainWindowState", "")
    if not encoded:
        return False
    try:
        from .qt import QtCore

        state = QtCore.QByteArray.fromBase64(encoded.encode("ascii"))
        if not gui.getMainWindow().restoreState(state):
            return False
    except Exception:
        return False
    workspace.RemString("PreviousMainWindowState")
    workspace.RemBool("WorkspaceLayoutCaptured")
    workspace.RemBool("StandardToolbarCaptured")
    return True


def _ensure_workspace_strip(app: Any, main_window: Any) -> Any:
    from .qt import QtWidgets

    toolbar = _find_child(main_window, QtWidgets.QToolBar, WORKSPACE_STRIP_NAME)
    if toolbar is not None:
        return toolbar

    toolbar = QtWidgets.QToolBar(f"{SHORT_NAME} Workspace", main_window)
    toolbar.setObjectName(WORKSPACE_STRIP_NAME)
    toolbar.setMovable(False)
    toolbar.setFloatable(False)

    workspace_label = QtWidgets.QLabel("WORKSPACE", toolbar)
    selector = QtWidgets.QComboBox(toolbar)
    selector.setObjectName(WORKSPACE_SELECTOR_NAME)
    selector.addItem("DESIGN")
    selector.setMinimumContentsLength(10)
    selector.setToolTip(f"{SHORT_NAME} Design workspace")
    toolbar.addWidget(workspace_label)
    toolbar.addWidget(selector)
    toolbar.addSeparator()

    tabs = QtWidgets.QTabBar(toolbar)
    tabs.setObjectName(WORKSPACE_TABS_NAME)
    tabs.setDrawBase(False)
    tabs.setExpanding(False)
    for category, _toolbar_titles in WORKSPACE_CATEGORIES:
        tabs.addTab(category)
    tabs.currentChanged.connect(
        partial(_workspace_category_changed, app, main_window)
    )
    toolbar.addWidget(tabs)
    return toolbar


def _workspace_category_changed(app: Any, main_window: Any, index: int) -> None:
    if index < 0 or index >= len(WORKSPACE_CATEGORIES):
        return
    category = WORKSPACE_CATEGORIES[index][0]
    parameters = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")
    parameters.SetString("FusionWorkspaceTab", category)
    _show_active_workspace_category(app, main_window)


def _show_active_workspace_category(app: Any, main_window: Any) -> None:
    from .qt import QtWidgets

    parameters = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")
    requested = parameters.GetString("FusionWorkspaceTab", WORKSPACE_CATEGORIES[0][0])
    category_index = next(
        (
            index
            for index, (category, _toolbar_titles) in enumerate(WORKSPACE_CATEGORIES)
            if category == requested
        ),
        0,
    )
    active_titles = set(WORKSPACE_CATEGORIES[category_index][1])
    for object_name, title in WORKSPACE_TOOLBAR_NAMES.items():
        toolbar = _find_child(main_window, QtWidgets.QToolBar, object_name)
        if toolbar is None:
            continue
        toolbar.setVisible(title in active_titles)

    tabs = _find_child(main_window, QtWidgets.QTabBar, WORKSPACE_TABS_NAME)
    if tabs is not None and tabs.currentIndex() != category_index:
        tabs.setCurrentIndex(category_index)


def _ensure_timeline_dock(app: Any, gui: Any, main_window: Any) -> Any:
    from .qt import QtCore, QtGui, QtWidgets, enum_value, standard_pixmap

    existing = _find_child(main_window, QtWidgets.QDockWidget, TIMELINE_DOCK_NAME)
    if existing is not None:
        return existing

    class FusionTimeline(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setObjectName(TIMELINE_WIDGET_NAME)
            self._signature: tuple[TimelineEntry, ...] | None = None

            self.empty = QtWidgets.QLabel(
                "Create a sketch or feature to begin the design history.",
                self,
            )
            self.empty.setAlignment(
                enum_value(QtCore.Qt, "AlignmentFlag", "AlignCenter")
            )

            navigation = QtWidgets.QWidget(self)
            navigation_layout = QtWidgets.QHBoxLayout(navigation)
            navigation_layout.setContentsMargins(6, 4, 6, 4)
            navigation_layout.setSpacing(2)
            navigation_layout.addWidget(QtWidgets.QLabel("VIEW", navigation))
            for label, command_id in NAVIGATION_COMMANDS:
                button = QtWidgets.QToolButton(navigation)
                button.setText(label)
                button.setToolTip(f"{label} view")
                action = _find_child(main_window, QtGui.QAction, command_id)
                if action is not None:
                    button.setIcon(action.icon())
                    button.setToolButtonStyle(
                        enum_value(
                            QtCore.Qt,
                            "ToolButtonStyle",
                            "ToolButtonIconOnly",
                        )
                    )
                else:
                    button.setToolButtonStyle(
                        enum_value(
                            QtCore.Qt,
                            "ToolButtonStyle",
                            "ToolButtonTextOnly",
                        )
                    )
                button.setAutoRaise(True)
                button.clicked.connect(
                    partial(_run_gui_command, gui, command_id)
                )
                navigation_layout.addWidget(button)

            self.items = QtWidgets.QWidget(self)
            self.item_layout = QtWidgets.QHBoxLayout(self.items)
            self.item_layout.setContentsMargins(6, 4, 6, 4)
            self.item_layout.setSpacing(4)
            self.item_layout.addStretch(1)

            self.scroll = QtWidgets.QScrollArea(self)
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(
                enum_value(QtWidgets.QFrame, "Shape", "NoFrame")
            )
            self.scroll.setHorizontalScrollBarPolicy(
                enum_value(
                    QtCore.Qt,
                    "ScrollBarPolicy",
                    "ScrollBarAsNeeded",
                )
            )
            self.scroll.setVerticalScrollBarPolicy(
                enum_value(
                    QtCore.Qt,
                    "ScrollBarPolicy",
                    "ScrollBarAlwaysOff",
                )
            )
            self.scroll.setWidget(self.items)

            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(navigation)
            layout.addWidget(self.empty, 1)
            layout.addWidget(self.scroll, 1)

            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(1200)
            self.timer.timeout.connect(self.refresh)
            self.timer.start()
            self._default_icon = self.style().standardIcon(
                standard_pixmap("SP_FileIcon")
            )
            self.refresh()

        def set_active(self, active: bool) -> None:
            if active:
                self._signature = None
                if not self.timer.isActive():
                    self.timer.start()
                self.refresh()
            else:
                self.timer.stop()

        def refresh(self) -> None:
            entries = timeline_entries(getattr(app, "ActiveDocument", None))
            if entries == self._signature:
                return
            self._signature = entries
            while self.item_layout.count() > 1:
                item = self.item_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

            self.empty.setVisible(not entries)
            self.scroll.setVisible(bool(entries))
            for entry in entries:
                button = QtWidgets.QToolButton(self.items)
                button.setText(entry.label)
                button.setToolTip(entry.type_id or entry.object_name)
                button.setIcon(self._default_icon)
                button.setIconSize(QtCore.QSize(20, 20))
                button.setToolButtonStyle(
                    enum_value(
                        QtCore.Qt,
                        "ToolButtonStyle",
                        "ToolButtonTextUnderIcon",
                    )
                )
                button.setMinimumWidth(72)
                button.clicked.connect(
                    partial(self._select_object, entry.object_name)
                )
                self.item_layout.insertWidget(self.item_layout.count() - 1, button)

        def _select_object(self, object_name: str, _checked: bool = False) -> None:
            document = getattr(app, "ActiveDocument", None)
            if document is None:
                return
            obj = document.getObject(object_name)
            if obj is None:
                return
            try:
                gui.Selection.clearSelection()
                gui.Selection.addSelection(obj)
            except Exception:
                return

    timeline = QtWidgets.QDockWidget("TIMELINE", main_window)
    timeline.setObjectName(TIMELINE_DOCK_NAME)
    timeline.setMinimumHeight(104)
    timeline.setWidget(FusionTimeline(timeline))
    return timeline


def _run_gui_command(gui: Any, command_id: str, _checked: bool = False) -> None:
    try:
        gui.runCommand(command_id)
    except Exception:
        return


def _hide_workspace_toolbars(gui: Any) -> None:
    """Hide only the add-on's optional grouped toolbars in default mode."""
    try:
        from .qt import QtWidgets

        main_window = gui.getMainWindow()
        for toolbar in main_window.findChildren(QtWidgets.QToolBar):
            if toolbar.objectName() in WORKSPACE_TOOLBAR_NAMES:
                toolbar.hide()
                toolbar.setProperty(TOOLBAR_CONFIGURED_PROPERTY, False)
    except Exception:
        return


def _hide_workspace_chrome(gui: Any) -> None:
    try:
        from .qt import QtWidgets

        main_window = gui.getMainWindow()
        for widget_type, object_name in (
            (QtWidgets.QToolBar, WORKSPACE_STRIP_NAME),
            (QtWidgets.QDockWidget, TIMELINE_DOCK_NAME),
        ):
            widget = _find_child(main_window, widget_type, object_name)
            if widget is not None:
                if object_name == TIMELINE_DOCK_NAME:
                    timeline = widget.widget()
                    if timeline is not None and hasattr(timeline, "set_active"):
                        timeline.set_active(False)
                widget.hide()
    except Exception:
        return


def _restore_model_dock_title(gui: Any) -> None:
    try:
        from .qt import QtWidgets

        model_dock = _find_model_dock(gui.getMainWindow(), QtWidgets.QDockWidget)
        if model_dock is None:
            return
        previous_title = model_dock.property(MODEL_DOCK_TITLE_PROPERTY)
        if previous_title is not None:
            model_dock.setWindowTitle(str(previous_title))
            model_dock.setProperty(MODEL_DOCK_TITLE_PROPERTY, None)
        _restore_minimum_width(
            model_dock,
            MODEL_DOCK_MIN_WIDTH_PROPERTY,
            clear=True,
        )
    except Exception:
        return


def _capture_minimum_width(widget: Any, property_name: str) -> None:
    if widget.property(property_name) is None:
        widget.setProperty(property_name, widget.minimumWidth())


def _restore_minimum_width(
    widget: Any,
    property_name: str,
    *,
    clear: bool,
) -> None:
    previous_width = widget.property(property_name)
    if previous_width is None:
        return
    widget.setMinimumWidth(int(previous_width))
    if clear:
        widget.setProperty(property_name, None)


def _prepare_docks_for_layout_restore(gui: Any, *, dock: Any = None) -> None:
    try:
        from .qt import QtWidgets

        main_window = gui.getMainWindow()
        model_dock = _find_model_dock(main_window, QtWidgets.QDockWidget)
        if model_dock is not None:
            _restore_minimum_width(
                model_dock,
                MODEL_DOCK_MIN_WIDTH_PROPERTY,
                clear=False,
            )
        assistant_dock = (
            dock
            if dock is not None
            else _find_child(
                main_window,
                QtWidgets.QDockWidget,
                ASSISTANT_DOCK_NAME,
            )
        )
        if assistant_dock is not None:
            _restore_minimum_width(
                assistant_dock,
                ASSISTANT_DOCK_MIN_WIDTH_PROPERTY,
                clear=False,
            )
    except Exception:
        return


def _restore_assistant_dock(gui: Any, *, dock: Any = None) -> None:
    try:
        from .qt import QtWidgets

        assistant_dock = (
            dock
            if dock is not None
            else _find_child(
                gui.getMainWindow(),
                QtWidgets.QDockWidget,
                ASSISTANT_DOCK_NAME,
            )
        )
        if assistant_dock is None:
            return
        assistant_dock.setProperty(DOCK_CONFIGURED_PROPERTY, False)
        _restore_minimum_width(
            assistant_dock,
            ASSISTANT_DOCK_MIN_WIDTH_PROPERTY,
            clear=True,
        )
        assistant_dock.setWindowTitle(SHORT_NAME)
    except Exception:
        return


def _apply_model_dock_title(model_dock: Any) -> None:
    try:
        model_dock.setWindowTitle("BROWSER")
    except Exception:
        return


def _restore_toolbar_style(gui: Any, *, show_standard: bool = False) -> None:
    try:
        from .qt import QtCore, QtWidgets, enum_value

        style = enum_value(QtCore.Qt, "ToolButtonStyle", "ToolButtonIconOnly")
        main_window = gui.getMainWindow()
        main_window.setProperty(WORKSPACE_CONFIGURED_PROPERTY, False)
        for toolbar in main_window.findChildren(QtWidgets.QToolBar):
            object_name = toolbar.objectName()
            display_title = WORKSPACE_TOOLBAR_NAMES.get(object_name)
            if display_title is not None:
                toolbar.setWindowTitle(display_title)
                toolbar.setToolButtonStyle(style)
                toolbar.setMovable(True)
                toolbar.setFloatable(True)
                toolbar.setProperty(TOOLBAR_CONFIGURED_PROPERTY, False)
                toolbar.hide()
            elif object_name == STANDARD_TOOLBAR_NAME:
                toolbar.setWindowTitle(STANDARD_TOOLBAR_TITLE)
                toolbar.setProperty(TOOLBAR_CONFIGURED_PROPERTY, False)
                if show_standard:
                    toolbar.show()
    except Exception:
        return


def _load_native_command_modules() -> None:
    for module_name in ("PartGui", "PartDesignGui", "SketcherGui"):
        try:
            __import__(module_name)
        except Exception:
            continue
