"""AgentCAD application composition for an active FreeCAD GUI process."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core.policy import ToolPolicy
from .core.project_store import ProjectStore
from .core.session import AgentSession
from .providers.codex import CodexProvider
from .providers.registry import ProviderRegistry
from .tools import build_registry
from .ui.qt import RIGHT_DOCK_AREA, QtCore
from .ui.settings import AgentCADSettings


class AgentCADApplication:
    """Own providers, session state, settings, and the native dock."""

    def __init__(self, app: Any, gui: Any) -> None:
        self.app = app
        self.gui = gui
        self.settings = AgentCADSettings(app)
        self.store = ProjectStore(Path(app.getUserAppDataDir()) / "AgentCAD")
        self.registry = build_registry()
        self.provider_registry = ProviderRegistry()
        self.current_provider = self._build_codex_provider()
        self.provider_registry.register(self.current_provider)
        self.session = AgentSession(
            app=app,
            gui=gui,
            store=self.store,
            registry=self.registry,
            provider=self.current_provider,
            policy=self._build_policy(),
        )
        self.dock = None

    @property
    def providers(self):
        return self.provider_registry.providers()

    def show_panel(self):
        from .ui.panel import AgentCADDock

        main_window = self.gui.getMainWindow()
        if self.dock is None:
            existing = main_window.findChild(AgentCADDock, "AgentCADDock")
            self.dock = existing or AgentCADDock(self, main_window)
            if existing is None:
                main_window.addDockWidget(RIGHT_DOCK_AREA, self.dock)
        self.dock.show()
        self.dock.raise_()
        self.dock.panel.refresh_project(force=True)
        return self.dock

    def select_provider(self, provider_id: str) -> None:
        provider = self.provider_registry.get(provider_id)
        self.current_provider = provider
        self.session.provider = provider

    def reload_settings(self) -> None:
        selected = self.current_provider.provider_id
        old_registry = self.provider_registry
        self.provider_registry = ProviderRegistry()
        self.current_provider = self._build_codex_provider()
        self.provider_registry.register(self.current_provider)
        self.session.provider = self.provider_registry.get(selected)
        self.session.policy = self._build_policy()
        old_registry.close()

    def shutdown(self) -> None:
        self.provider_registry.close()

    def _build_codex_provider(self) -> CodexProvider:
        value = self.settings.snapshot()
        return CodexProvider(
            executable=value.codex_executable or None,
            model=value.codex_model or None,
            reasoning_effort=value.reasoning_effort,
            state_dir=self.store.data_root / "codex-home",
        )

    def _build_policy(self) -> ToolPolicy:
        return ToolPolicy(
            allow_safe_writes=self.settings.snapshot().allow_safe_writes,
        )


_application: AgentCADApplication | None = None


def get_application() -> AgentCADApplication:
    global _application
    if _application is None:
        import FreeCADGui as Gui  # type: ignore[import-not-found]

        # isort: split
        import FreeCAD as App  # type: ignore[import-not-found]

        _application = AgentCADApplication(App, Gui)
        qt_application = QtCore.QCoreApplication.instance()
        if qt_application is not None:
            qt_application.aboutToQuit.connect(_application.shutdown)
    return _application
