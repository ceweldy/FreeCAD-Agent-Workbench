from __future__ import annotations

import sys
from types import SimpleNamespace

from freecad.AgentCAD.ui.workspace import (
    ASSISTANT_DOCK_MIN_WIDTH_PROPERTY,
    FUSION_NAVIGATION_STYLE,
    MODEL_DOCK_MIN_WIDTH_PROPERTY,
    STANDARD_TOOLBAR_NAME,
    TIMELINE_AUXILIARY_NAMES,
    TIMELINE_DOCK_NAME,
    WORKBENCH_NAME,
    WORKSPACE_CATEGORIES,
    WORKSPACE_STRIP_NAME,
    WORKSPACE_TOOLBAR_NAMES,
    CommandAlias,
    NativeCommandAlias,
    _apply_preferences,
    _capture_workspace_layout,
    _find_model_dock,
    _prepare_docks_for_layout_restore,
    _restore_preferences,
    _restore_toolbar_style,
    _show_active_workspace_category,
    _style_workspace,
    available_toolbar_groups,
    configure_workspace,
    timeline_entries,
)


class Parameters:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def GetString(self, name: str, default: str = "") -> str:
        return str(self.values.get(name, default))

    def SetString(self, name: str, value: str) -> None:
        self.values[name] = value

    def SetBool(self, name: str, value: bool) -> None:
        self.values[name] = value

    def GetBool(self, name: str, default: bool = False) -> bool:
        return bool(self.values.get(name, default))

    def RemString(self, name: str) -> None:
        self.values.pop(name, None)

    def RemBool(self, name: str) -> None:
        self.values.pop(name, None)


def parameter_app() -> tuple[object, dict[str, Parameters]]:
    groups: dict[str, Parameters] = {}

    def param_get(path: str) -> Parameters:
        return groups.setdefault(path, Parameters())

    return SimpleNamespace(ParamGet=param_get), groups


def test_fusion_profile_applies_and_restores_user_preferences() -> None:
    app, _groups = parameter_app()
    view = app.ParamGet("User parameter:BaseApp/Preferences/View")
    general = app.ParamGet("User parameter:BaseApp/Preferences/General")
    view.SetString("NavigationStyle", "Gui::CADNavigationStyle")
    view.SetBool("ZoomAtCursor", False)
    view.SetBool("ShowNaviCube", False)
    general.SetString("AutoloadModule", "$LastModule")

    _apply_preferences(app)

    assert view.GetString("NavigationStyle") == FUSION_NAVIGATION_STYLE
    assert view.values["ZoomAtCursor"] is True
    assert view.values["ShowNaviCube"] is True
    assert general.GetString("AutoloadModule") == WORKBENCH_NAME

    _restore_preferences(app)

    assert view.GetString("NavigationStyle") == "Gui::CADNavigationStyle"
    assert view.GetBool("ZoomAtCursor") is False
    assert view.GetBool("ShowNaviCube") is False
    assert general.GetString("AutoloadModule") == "$LastModule"

    view.SetString("NavigationStyle", "Gui::OpenInventorNavigationStyle")
    general.SetString("AutoloadModule", "PartWorkbench")
    _restore_preferences(app)

    assert view.GetString("NavigationStyle") == "Gui::OpenInventorNavigationStyle"
    assert general.GetString("AutoloadModule") == "PartWorkbench"


def test_fusion_profile_restores_empty_string_preferences() -> None:
    app, _groups = parameter_app()
    view = app.ParamGet("User parameter:BaseApp/Preferences/View")
    general = app.ParamGet("User parameter:BaseApp/Preferences/General")
    view.SetString("NavigationStyle", "")
    general.SetString("AutoloadModule", "")

    _apply_preferences(app)
    _restore_preferences(app)

    assert view.GetString("NavigationStyle") == ""
    assert general.GetString("AutoloadModule") == ""


def test_toolbar_groups_hide_unavailable_native_commands() -> None:
    groups = available_toolbar_groups(
        {"AgentCAD_NewComponent", "PartDesign_CompDatums", "AgentCAD_OpenAssistant"}
    )
    assert groups == (
        ("AgentCAD_CREATE_Toolbar", ("AgentCAD_NewComponent",)),
        ("AgentCAD_CONSTRUCT_Toolbar", ("PartDesign_CompDatums",)),
        ("AgentCAD_AGENT_Toolbar", ("AgentCAD_OpenAssistant",)),
    )


def test_timeline_preserves_feature_order_and_hides_origin_helpers() -> None:
    objects = [
        SimpleNamespace(Name=name, Label=name.replace("_", " "), TypeId="App::Plane")
        for name in TIMELINE_AUXILIARY_NAMES
    ]
    objects.extend(
        [
            SimpleNamespace(Name="Body", Label="Main Component", TypeId="PartDesign::Body"),
            SimpleNamespace(Name="Sketch", Label="", TypeId="Sketcher::SketchObject"),
            SimpleNamespace(Name="Pad", Label="Base Extrude", TypeId="PartDesign::Feature"),
        ]
    )

    assert [
        (entry.object_name, entry.label, entry.type_id)
        for entry in timeline_entries(SimpleNamespace(Objects=objects))
    ] == [
        ("Body", "Main Component", "PartDesign::Body"),
        ("Sketch", "Sketch", "Sketcher::SketchObject"),
        ("Pad", "Base Extrude", "PartDesign::Feature"),
    ]


def test_timeline_is_empty_without_an_active_document() -> None:
    assert timeline_entries(None) == ()


def test_native_alias_uses_the_target_commands_live_active_state(monkeypatch) -> None:
    calls: list[str] = []
    gui = SimpleNamespace(
        isCommandActive=lambda command_id: calls.append(command_id) or True
    )
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    alias = NativeCommandAlias(
        CommandAlias("AgentCAD_Test", "PartDesign_Test", "Test", "Test", "")
    )

    assert alias.IsActive() is True
    assert calls == ["PartDesign_Test"]


def test_native_alias_is_inactive_when_target_command_check_fails(monkeypatch) -> None:
    def fail(_command_id: str):
        raise RuntimeError("command unavailable")

    gui = SimpleNamespace(isCommandActive=fail)
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    alias = NativeCommandAlias(
        CommandAlias("AgentCAD_Test", "PartDesign_Test", "Test", "Test", "")
    )

    assert alias.IsActive() is False


def test_find_model_dock_supports_current_and_legacy_freecad_names() -> None:
    dock = object()
    main_window = SimpleNamespace(
        findChild=lambda _dock_type, name: dock if name == "Combo View" else None
    )

    assert _find_model_dock(main_window, object) is dock


def test_workspace_releases_dock_constraints_before_layout_restore(
    monkeypatch,
) -> None:
    model_properties = {MODEL_DOCK_MIN_WIDTH_PROPERTY: 110}
    assistant_properties = {ASSISTANT_DOCK_MIN_WIDTH_PROPERTY: 190}
    calls: list[tuple[str, int]] = []
    model_dock = SimpleNamespace(
        property=lambda name: model_properties.get(name),
        setMinimumWidth=lambda width: calls.append(("model", width)),
    )
    assistant_dock = SimpleNamespace(
        property=lambda name: assistant_properties.get(name),
        setMinimumWidth=lambda width: calls.append(("assistant", width)),
    )
    main_window = SimpleNamespace(
        findChild=lambda _kind, name: model_dock if name == "Model" else None
    )
    gui = SimpleNamespace(getMainWindow=lambda: main_window)
    monkeypatch.setitem(
        sys.modules,
        "freecad.AgentCAD.ui.qt",
        SimpleNamespace(QtWidgets=SimpleNamespace(QDockWidget=object)),
    )

    _prepare_docks_for_layout_restore(gui, dock=assistant_dock)

    assert calls == [("model", 110), ("assistant", 190)]
    assert model_properties[MODEL_DOCK_MIN_WIDTH_PROPERTY] == 110
    assert assistant_properties[ASSISTANT_DOCK_MIN_WIDTH_PROPERTY] == 190


def test_workspace_layout_is_captured_only_once() -> None:
    app, _groups = parameter_app()

    class WindowState:
        @staticmethod
        def toBase64() -> bytes:
            return b"first-layout"

    main_window = SimpleNamespace(saveState=WindowState)
    _capture_workspace_layout(app, main_window)
    workspace = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")

    assert workspace.GetString("PreviousMainWindowState") == "first-layout"
    assert workspace.GetBool("WorkspaceLayoutCaptured") is True
    assert workspace.GetBool("StandardToolbarCaptured") is False

    main_window.saveState = lambda: SimpleNamespace(toBase64=lambda: b"second-layout")
    _capture_workspace_layout(app, main_window)

    assert workspace.GetString("PreviousMainWindowState") == "first-layout"


def test_workspace_snapshot_records_existing_standard_toolbar() -> None:
    app, _groups = parameter_app()

    class WindowState:
        @staticmethod
        def toBase64() -> bytes:
            return b"layout-with-toolbar"

    _capture_workspace_layout(
        app,
        SimpleNamespace(saveState=WindowState),
        standard_toolbar_present=True,
    )
    workspace = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")

    assert workspace.GetBool("StandardToolbarCaptured") is True


def test_workspace_reapplication_does_not_rearrange_accessible_widgets(monkeypatch) -> None:
    app, _groups = parameter_app()
    calls: list[object] = []
    toolbar_properties: dict[str, object] = {}
    toolbar = SimpleNamespace(
        objectName=lambda: "AgentCAD_CREATE_Toolbar",
        setWindowTitle=lambda title: calls.append(("title", title)),
        property=lambda name: toolbar_properties.get(name, False),
        setProperty=lambda name, value: toolbar_properties.__setitem__(name, value),
        setToolButtonStyle=lambda _style: calls.append("toolbar-style"),
        setIconSize=lambda size: calls.append(("icon-size", size)),
        setMovable=lambda movable: calls.append(("movable", movable)),
        setFloatable=lambda floatable: calls.append(("floatable", floatable)),
        show=lambda: calls.append("toolbar-show"),
    )
    dock = SimpleNamespace(
        property=lambda name: toolbar_properties.get(f"dock-{name}", False),
        setProperty=lambda name, value: toolbar_properties.__setitem__(f"dock-{name}", value),
        setMinimumWidth=lambda width: calls.append(("width", width)),
        setWindowTitle=lambda title: calls.append(("dock-title", title)),
        show=lambda: calls.append("dock-show"),
    )
    main_window = SimpleNamespace(
        property=lambda _name: True,
        saveState=lambda: (_ for _ in ()).throw(AssertionError("layout recaptured")),
        findChildren=lambda _kind: [toolbar],
        addDockWidget=lambda area, item: calls.append(("dock-area", area, item)),
    )
    gui = SimpleNamespace(getMainWindow=lambda: main_window)
    qt_module = SimpleNamespace(
        QtCore=SimpleNamespace(Qt=object(), QSize=lambda width, height: (width, height)),
        QtWidgets=SimpleNamespace(QDockWidget=object, QTabBar=object, QToolBar=object),
        enum_value=lambda *_args: "area",
    )
    monkeypatch.setitem(sys.modules, "freecad.AgentCAD.ui.qt", qt_module)

    _style_workspace(app, gui, dock=dock)
    _style_workspace(app, gui, dock=dock)

    assert calls == [
        ("title", "CREATE"),
        "toolbar-style",
        ("icon-size", (28, 28)),
        ("movable", False),
        ("floatable", False),
        ("dock-area", "area", dock),
        ("dock-title", "AGENT WORKBENCH"),
        ("width", 340),
        "dock-show",
        ("title", "CREATE"),
        ("dock-title", "AGENT WORKBENCH"),
        ("width", 340),
        "dock-show",
    ]


def test_workspace_styles_only_namespaced_agentcad_toolbars(monkeypatch) -> None:
    owned_calls: list[object] = []
    unrelated_calls: list[object] = []

    def toolbar(object_name: str, calls: list[object]) -> SimpleNamespace:
        properties: dict[str, object] = {}
        return SimpleNamespace(
            objectName=lambda: object_name,
            windowTitle=lambda: "CREATE",
            setWindowTitle=lambda title: calls.append(("title", title)),
            property=lambda name: properties.get(name, False),
            setProperty=lambda name, value: properties.__setitem__(name, value),
            setToolButtonStyle=lambda style: calls.append(("style", style)),
            setIconSize=lambda size: calls.append(("size", size)),
            setMovable=lambda movable: calls.append(("movable", movable)),
            setFloatable=lambda floatable: calls.append(("floatable", floatable)),
            show=lambda: calls.append("show"),
            hide=lambda: calls.append("hide"),
        )

    owned = toolbar("AgentCAD_CREATE_Toolbar", owned_calls)
    unrelated = toolbar("OtherExtension_CREATE_Toolbar", unrelated_calls)
    toolbars = [unrelated]
    main_window = SimpleNamespace(
        property=lambda _name: True,
        setProperty=lambda _name, _value: None,
        findChildren=lambda _kind: toolbars,
    )
    gui = SimpleNamespace(getMainWindow=lambda: main_window)
    qt_module = SimpleNamespace(
        QtCore=SimpleNamespace(Qt=object(), QSize=lambda width, height: (width, height)),
        QtWidgets=SimpleNamespace(QDockWidget=object, QToolBar=object),
        enum_value=lambda *_args: "style",
    )
    monkeypatch.setitem(sys.modules, "freecad.AgentCAD.ui.qt", qt_module)

    _style_workspace(SimpleNamespace(), gui)
    toolbars.append(owned)
    _style_workspace(SimpleNamespace(), gui)
    _style_workspace(SimpleNamespace(), gui)

    assert unrelated_calls == []
    assert owned_calls == [
        ("title", WORKSPACE_TOOLBAR_NAMES["AgentCAD_CREATE_Toolbar"]),
        ("style", "style"),
        ("size", (28, 28)),
        ("movable", False),
        ("floatable", False),
        ("title", WORKSPACE_TOOLBAR_NAMES["AgentCAD_CREATE_Toolbar"]),
    ]
    assert STANDARD_TOOLBAR_NAME not in WORKSPACE_TOOLBAR_NAMES


def test_disabled_workspace_hides_owned_fusion_toolbars_after_layout_restore(
    monkeypatch,
) -> None:
    calls: list[object] = []
    toolbar = SimpleNamespace(
        objectName=lambda: "AgentCAD_CREATE_Toolbar",
        setWindowTitle=lambda title: calls.append(("title", title)),
        setToolButtonStyle=lambda style: calls.append(("style", style)),
        setMovable=lambda movable: calls.append(("movable", movable)),
        setFloatable=lambda floatable: calls.append(("floatable", floatable)),
        setProperty=lambda name, value: calls.append(("property", name, value)),
        hide=lambda: calls.append("hide"),
        show=lambda: calls.append("show"),
    )
    main_window = SimpleNamespace(
        setProperty=lambda name, value: calls.append(("window-property", name, value)),
        findChildren=lambda _kind: [toolbar],
    )
    gui = SimpleNamespace(getMainWindow=lambda: main_window)
    qt_module = SimpleNamespace(
        QtCore=SimpleNamespace(Qt=object()),
        QtWidgets=SimpleNamespace(QToolBar=object),
        enum_value=lambda *_args: "icon-only",
    )
    monkeypatch.setitem(sys.modules, "freecad.AgentCAD.ui.qt", qt_module)

    _restore_toolbar_style(gui)

    assert calls[-1] == "hide"
    assert "show" not in calls


def test_workspace_category_persists_and_controls_owned_toolbars(monkeypatch) -> None:
    app, _groups = parameter_app()
    visibility: dict[str, bool] = {}
    toolbars = {
        object_name: SimpleNamespace(
            setVisible=lambda visible, name=object_name: visibility.__setitem__(
                name,
                visible,
            )
        )
        for object_name in WORKSPACE_TOOLBAR_NAMES
    }
    tabs = SimpleNamespace(
        currentIndex=lambda: 0,
        setCurrentIndex=lambda index: visibility.__setitem__("tab", index),
    )

    def find_child(widget_type, object_name):
        if widget_type is object and object_name == "AgentCADWorkspaceTabs":
            return tabs
        return toolbars.get(object_name)

    main_window = SimpleNamespace(findChild=find_child)
    monkeypatch.setitem(
        sys.modules,
        "freecad.AgentCAD.ui.qt",
        SimpleNamespace(QtWidgets=SimpleNamespace(QToolBar=object, QTabBar=object)),
    )
    parameters = app.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentCAD")
    parameters.SetString("FusionWorkspaceTab", WORKSPACE_CATEGORIES[2][0])

    _show_active_workspace_category(app, main_window)

    assert visibility["AgentCAD_INSPECT_Toolbar"] is True
    assert visibility["AgentCAD_CREATE_Toolbar"] is False
    assert visibility["AgentCAD_AGENT_Toolbar"] is False
    assert visibility["tab"] == 2


def test_default_disabled_workspace_preserves_user_toolbar_visibility(monkeypatch) -> None:
    app, _groups = parameter_app()
    standard_calls: list[object] = []
    grouped_calls: list[object] = []
    standard = SimpleNamespace(
        objectName=lambda: STANDARD_TOOLBAR_NAME,
        setWindowTitle=lambda title: standard_calls.append(("title", title)),
        setProperty=lambda name, value: standard_calls.append(("property", name, value)),
        show=lambda: standard_calls.append("show"),
        hide=lambda: standard_calls.append("hide"),
    )
    grouped = SimpleNamespace(
        objectName=lambda: "AgentCAD_CREATE_Toolbar",
        setProperty=lambda name, value: grouped_calls.append(("property", name, value)),
        hide=lambda: grouped_calls.append("hide"),
    )
    hidden_chrome: list[str] = []
    timeline_active: list[bool] = []

    def find_child(_kind, name):
        if name == WORKSPACE_STRIP_NAME:
            return SimpleNamespace(hide=lambda: hidden_chrome.append(name))
        if name == TIMELINE_DOCK_NAME:
            return SimpleNamespace(
                hide=lambda: hidden_chrome.append(name),
                widget=lambda: SimpleNamespace(
                    set_active=lambda active: timeline_active.append(active)
                ),
            )
        return None

    main_window = SimpleNamespace(
        findChildren=lambda _kind: [standard, grouped],
        findChild=find_child,
    )
    gui = SimpleNamespace(getMainWindow=lambda: main_window)
    monkeypatch.setitem(
        sys.modules,
        "freecad.AgentCAD.ui.qt",
        SimpleNamespace(
            QtWidgets=SimpleNamespace(
                QDockWidget=object,
                QToolBar=object,
            )
        ),
    )

    configure_workspace(app, gui, enabled=False)

    assert standard_calls == []
    assert "hide" in grouped_calls
    assert hidden_chrome == [WORKSPACE_STRIP_NAME, TIMELINE_DOCK_NAME]
    assert timeline_active == [False]
