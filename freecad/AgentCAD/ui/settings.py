"""Agent Workbench preferences and native settings dialog."""

from __future__ import annotations

from dataclasses import dataclass

from .. import SHORT_NAME
from .qt import QtWidgets, dialog_button

PARAMETER_PATH = "User parameter:BaseApp/Preferences/Mod/AgentCAD"


@dataclass(frozen=True)
class SettingsSnapshot:
    codex_executable: str
    codex_model: str
    reasoning_effort: str
    auto_open: bool
    allow_safe_writes: bool
    fusion_workspace: bool


class AgentCADSettings:
    """Typed access to FreeCAD's persistent parameter store."""

    def __init__(self, app) -> None:
        self._parameters = app.ParamGet(PARAMETER_PATH)

    def snapshot(self) -> SettingsSnapshot:
        effort = self._parameters.GetString("ReasoningEffort", "high")
        if effort not in {"low", "medium", "high", "xhigh"}:
            effort = "high"
        return SettingsSnapshot(
            codex_executable=self._parameters.GetString("CodexExecutable", "").strip(),
            codex_model=self._parameters.GetString("CodexModel", "").strip(),
            reasoning_effort=effort,
            auto_open=self._parameters.GetBool("AutoOpen", True),
            allow_safe_writes=self._parameters.GetBool("AllowSafeWrites", True),
            fusion_workspace=self._parameters.GetBool("FusionWorkspace", False),
        )

    def save(self, value: SettingsSnapshot) -> None:
        self._parameters.SetString("CodexExecutable", value.codex_executable.strip())
        self._parameters.SetString("CodexModel", value.codex_model.strip())
        self._parameters.SetString("ReasoningEffort", value.reasoning_effort)
        self._parameters.SetBool("AutoOpen", bool(value.auto_open))
        self._parameters.SetBool("AllowSafeWrites", bool(value.allow_safe_writes))
        self._parameters.SetBool("FusionWorkspace", bool(value.fusion_workspace))


class SettingsDialog(QtWidgets.QDialog):
    """Native form for local provider and safety preferences."""

    def __init__(self, settings: AgentCADSettings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        value = settings.snapshot()
        self.setWindowTitle(f"{SHORT_NAME} Settings")
        self.setModal(True)

        self.executable = QtWidgets.QLineEdit(value.codex_executable)
        self.executable.setPlaceholderText("Use codex from PATH")
        executable_row = QtWidgets.QWidget(self)
        executable_layout = QtWidgets.QHBoxLayout(executable_row)
        executable_layout.setContentsMargins(0, 0, 0, 0)
        executable_layout.addWidget(self.executable)
        browse = QtWidgets.QPushButton("Browse", executable_row)
        browse.clicked.connect(self._browse_executable)
        executable_layout.addWidget(browse)

        self.model = QtWidgets.QLineEdit(value.codex_model)
        self.model.setPlaceholderText("Use the Codex default model")
        self.effort = QtWidgets.QComboBox(self)
        self.effort.addItems(["low", "medium", "high", "xhigh"])
        self.effort.setCurrentText(value.reasoning_effort)
        self.auto_open = QtWidgets.QCheckBox(
            f"Open {SHORT_NAME} when FreeCAD starts",
            self,
        )
        self.auto_open.setChecked(value.auto_open)
        self.safe_writes = QtWidgets.QCheckBox(
            "Allow rollback-protected CAD changes",
            self,
        )
        self.safe_writes.setChecked(value.allow_safe_writes)
        self.fusion_workspace = QtWidgets.QCheckBox(
            "Use familiar Design workspace and navigation",
            self,
        )
        self.fusion_workspace.setChecked(value.fusion_workspace)

        form = QtWidgets.QFormLayout()
        form.addRow("Codex executable", executable_row)
        form.addRow("Model", self.model)
        form.addRow("Reasoning effort", self.effort)

        note = QtWidgets.QLabel(
            f"{SHORT_NAME} uses Codex-managed authentication in a private local profile.",
            self,
        )
        note.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox(
            dialog_button("Save") | dialog_button("Cancel"),
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.auto_open)
        layout.addWidget(self.safe_writes)
        layout.addWidget(self.fusion_workspace)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def accept(self) -> None:
        self._settings.save(
            SettingsSnapshot(
                codex_executable=self.executable.text(),
                codex_model=self.model.text(),
                reasoning_effort=self.effort.currentText(),
                auto_open=self.auto_open.isChecked(),
                allow_safe_writes=self.safe_writes.isChecked(),
                fusion_workspace=self.fusion_workspace.isChecked(),
            )
        )
        super().accept()

    def _browse_executable(self) -> None:
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Choose Codex executable",
            self.executable.text(),
        )
        if selected:
            self.executable.setText(selected)
