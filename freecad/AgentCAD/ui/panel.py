"""Native Agent Workbench dock, conversation panel, and background turn workers."""

from __future__ import annotations

import html
import threading

from .. import SHORT_NAME
from ..core.memory import IntentMemory
from .dispatcher import MainThreadDispatcher
from .qt import (
    CURSOR_END,
    USER_ROLE,
    QtCore,
    QtWidgets,
    Signal,
    Slot,
    exec_dialog,
    open_external_url,
    standard_pixmap,
)
from .settings import SettingsDialog


class _TurnWorker(QtCore.QObject):
    event = Signal(object)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        session,
        prompt: str,
        dispatcher: MainThreadDispatcher,
        expected_document_path: str,
    ) -> None:
        super().__init__()
        self._session = session
        self._prompt = prompt
        self._dispatcher = dispatcher
        self._expected_document_path = expected_document_path
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = self._session.run(
                self._prompt,
                dispatch=self._dispatcher.dispatch,
                on_event=self.event.emit,
                cancel_event=self.cancel_event,
                expected_document_path=self._expected_document_path,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class _StatusWorker(QtCore.QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, provider) -> None:
        super().__init__()
        self._provider = provider

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self._provider.status())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class _LoginWorker(QtCore.QObject):
    challenge = Signal(object)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, provider) -> None:
        super().__init__()
        self._provider = provider
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            status = self._provider.login_chatgpt(
                on_challenge=self.challenge.emit,
                cancel_event=self.cancel_event,
            )
            self.completed.emit(status)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class AgentCADDock(QtWidgets.QDockWidget):
    """A standard FreeCAD dock containing chat and durable memory views."""

    def __init__(self, application, parent=None) -> None:
        super().__init__(SHORT_NAME, parent)
        self.setObjectName("AgentCADDock")
        self.application = application
        self.panel = AgentCADPanel(application, self)
        self.setWidget(self.panel)

    def closeEvent(self, event) -> None:
        self.panel.request_stop()
        super().closeEvent(event)


class AgentCADPanel(QtWidgets.QWidget):
    """Conversation-first interface backed by the active FreeCAD document."""

    def __init__(self, application, parent=None) -> None:
        super().__init__(parent)
        self.application = application
        self.dispatcher = MainThreadDispatcher(self)
        self._project_path = ""
        self._populating_conversations = False
        self._received_delta = False
        self._turn_project = None
        self._deferred_project_refresh = False
        self._turn_thread = None
        self._turn_worker = None
        self._status_thread = None
        self._status_worker = None
        self._login_thread = None
        self._login_worker = None
        self._build_ui()

        self._document_timer = QtCore.QTimer(self)
        self._document_timer.setInterval(1500)
        self._document_timer.timeout.connect(self._check_document)
        self._document_timer.start()
        self.refresh_project(force=True)
        self.refresh_provider_status()

    def _build_ui(self) -> None:
        self.conversations = QtWidgets.QComboBox(self)
        self.conversations.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            if hasattr(QtWidgets.QComboBox, "SizeAdjustPolicy")
            else QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.conversations.setMinimumContentsLength(18)
        self.conversations.currentIndexChanged.connect(self._activate_conversation)

        self.new_task = QtWidgets.QPushButton("New task", self)
        self.new_task.clicked.connect(self._new_conversation)
        self.settings_button = QtWidgets.QPushButton("Settings", self)
        self.settings_button.clicked.connect(self._open_settings)

        conversation_row = QtWidgets.QHBoxLayout()
        conversation_row.addWidget(self.conversations, 1)
        conversation_row.addWidget(self.new_task)
        conversation_row.addWidget(self.settings_button)

        self.providers = QtWidgets.QComboBox(self)
        self.providers.currentIndexChanged.connect(self._select_provider)
        self.status = QtWidgets.QLabel("Checking Codex connection", self)
        self.status.setWordWrap(True)
        self.refresh_status = QtWidgets.QToolButton(self)
        self.refresh_status.setToolTip("Refresh provider status")
        self.refresh_status.setIcon(
            self.style().standardIcon(standard_pixmap("SP_BrowserReload"))
        )
        self.refresh_status.clicked.connect(self.refresh_provider_status)
        self.connect_provider = QtWidgets.QPushButton("Connect", self)
        self.connect_provider.setToolTip(
            f"Connect {SHORT_NAME} to ChatGPT through Codex"
        )
        self.connect_provider.clicked.connect(self._connect_codex)

        provider_row = QtWidgets.QHBoxLayout()
        provider_row.addWidget(self.providers)
        provider_row.addWidget(self.status, 1)
        provider_row.addWidget(self.connect_provider)
        provider_row.addWidget(self.refresh_status)

        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.addTab(self._chat_tab(), "Chat")
        self.tabs.addTab(self._memory_tab(), "Memory")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(conversation_row)
        layout.addLayout(provider_row)
        layout.addWidget(self.tabs, 1)
        self._refresh_provider_choices()

    def _chat_tab(self):
        page = QtWidgets.QWidget(self)
        self.transcript = QtWidgets.QTextBrowser(page)
        self.transcript.setOpenExternalLinks(False)

        self.activity = QtWidgets.QLabel("Ready", page)
        self.progress = QtWidgets.QProgressBar(page)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        activity_row = QtWidgets.QHBoxLayout()
        activity_row.addWidget(self.progress)
        activity_row.addWidget(self.activity, 1)

        self.prompt = QtWidgets.QPlainTextEdit(page)
        self.prompt.setPlaceholderText(
            "Describe what to inspect, create, measure, or remember in this model"
        )
        self.prompt.setMaximumBlockCount(200)

        self.send = QtWidgets.QPushButton("Send", page)
        self.send.setDefault(True)
        self.send.clicked.connect(self._send_prompt)
        self.stop = QtWidgets.QPushButton("Stop", page)
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.request_stop)
        action_row = QtWidgets.QHBoxLayout()
        action_row.addStretch(1)
        action_row.addWidget(self.stop)
        action_row.addWidget(self.send)

        layout = QtWidgets.QVBoxLayout(page)
        layout.addWidget(self.transcript, 1)
        layout.addLayout(activity_row)
        layout.addWidget(self.prompt)
        layout.addLayout(action_row)
        return page

    def _memory_tab(self):
        page = QtWidgets.QWidget(self)
        explanation = QtWidgets.QLabel(
            f"Durable project facts that {SHORT_NAME} carries into future tasks.",
            page,
        )
        explanation.setWordWrap(True)
        self.memory = QtWidgets.QTreeWidget(page)
        self.memory.setHeaderLabels(["Type", "Statement", "Authority"])
        self.memory.setRootIsDecorated(False)
        self.memory.setAlternatingRowColors(True)

        self.refresh_memory_button = QtWidgets.QPushButton("Refresh memory", page)
        self.refresh_memory_button.clicked.connect(self.refresh_memory)

        layout = QtWidgets.QVBoxLayout(page)
        layout.addWidget(explanation)
        layout.addWidget(self.memory, 1)
        layout.addWidget(self.refresh_memory_button)
        return page

    def refresh_project(self, *, force: bool = False) -> None:
        project = self.application.session.project_for_active_document()
        path = str(project.document_path) if project is not None else ""
        # This guard must stay before interrupted-turn recovery. A live turn is
        # intentionally persisted as user-only until its provider response arrives.
        if self._turn_worker is not None:
            if path != self._project_path:
                self._deferred_project_refresh = True
            return
        if not force and path == self._project_path:
            return
        self._project_path = path
        recovered = project.recover_interrupted_conversations() if project is not None else []
        self._populate_conversations(project)
        self._render_conversation(project)
        self.refresh_memory(project)
        enabled = project is not None and not self._provider_operation_active()
        self.conversations.setEnabled(enabled)
        self.new_task.setEnabled(enabled)
        self.send.setEnabled(enabled)
        self.prompt.setEnabled(enabled)
        if project is None:
            self.activity.setText("Save the active FreeCAD document to start a task")
        elif recovered:
            noun = "task" if len(recovered) == 1 else "tasks"
            self.activity.setText(
                f"Recovered {len(recovered)} interrupted {noun}; review the conversation"
            )
        elif self._turn_worker is None:
            self.activity.setText("Ready")

    def refresh_memory(self, project=None) -> None:
        project = project or self.application.session.project_for_active_document()
        self.memory.clear()
        if project is None:
            return
        for entry in IntentMemory(project).active_entries():
            item = QtWidgets.QTreeWidgetItem(
                [
                    str(entry.get("category", "")).replace("_", " ").title(),
                    str(entry.get("statement", "")),
                    str(entry.get("authority", "")).title(),
                ]
            )
            item.setToolTip(1, str(entry.get("statement", "")))
            self.memory.addTopLevelItem(item)
        self.memory.resizeColumnToContents(0)
        self.memory.resizeColumnToContents(2)

    def refresh_provider_status(self) -> None:
        if self._provider_operation_active():
            return
        provider = self.application.current_provider
        self.status.setText("Checking provider connection")
        self.refresh_status.setEnabled(False)
        thread = QtCore.QThread(self)
        worker = _StatusWorker(provider)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._provider_status_ready)
        worker.failed.connect(self._provider_status_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._status_thread_finished)
        self._status_thread = thread
        self._status_worker = worker
        self._set_running(False)
        thread.start()

    @Slot(object)
    def _provider_status_ready(self, status) -> None:
        self.status.setText(status.message)
        self.status.setToolTip(status.version or "")
        self.connect_provider.setVisible(not status.ready)

    @Slot(str)
    def _provider_status_failed(self, message: str) -> None:
        self.status.setText(f"Provider check failed: {message}")

    @Slot()
    def _status_thread_finished(self) -> None:
        self._status_thread = None
        self._status_worker = None
        self._set_running(self._turn_worker is not None)

    def _connect_codex(self) -> None:
        if self._provider_operation_active():
            return
        self.status.setText("Starting secure ChatGPT sign-in")
        thread = QtCore.QThread(self)
        worker = _LoginWorker(self.application.current_provider)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.challenge.connect(self._open_login_challenge)
        worker.completed.connect(self._login_completed)
        worker.failed.connect(self._login_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._login_thread_finished)
        self._login_thread = thread
        self._login_worker = worker
        self._set_running(False)
        thread.start()

    @Slot(object)
    def _open_login_challenge(self, challenge) -> None:
        auth_url = str(challenge.get("auth_url") or "")
        if not open_external_url(auth_url):
            worker = self._login_worker
            if worker is not None:
                worker.cancel_event.set()
            self._show_error(
                f"{SHORT_NAME} could not open the ChatGPT sign-in page in your browser"
            )
            return
        self.status.setText("Finish signing in to ChatGPT in your browser")

    @Slot(object)
    def _login_completed(self, status) -> None:
        self._provider_status_ready(status)

    @Slot(str)
    def _login_failed(self, message: str) -> None:
        self.status.setText(f"Codex connection failed: {message}")

    @Slot()
    def _login_thread_finished(self) -> None:
        self._login_thread = None
        self._login_worker = None
        self._set_running(self._turn_worker is not None)

    def _refresh_provider_choices(self) -> None:
        selected = self.application.current_provider.provider_id
        self.providers.blockSignals(True)
        self.providers.clear()
        for provider in self.application.providers:
            self.providers.addItem(provider.display_name, provider.provider_id)
        index = self.providers.findData(selected, USER_ROLE)
        self.providers.setCurrentIndex(max(0, index))
        self.providers.blockSignals(False)

    @Slot(int)
    def _select_provider(self, index: int) -> None:
        if self._provider_operation_active():
            return
        provider_id = self.providers.itemData(index, USER_ROLE)
        if provider_id:
            self.application.select_provider(str(provider_id))
            self.refresh_provider_status()

    def _check_document(self) -> None:
        project = self.application.session.project_for_active_document()
        path = str(project.document_path) if project is not None else ""
        if path != self._project_path:
            if self._turn_worker is not None:
                self._deferred_project_refresh = True
                return
            self.refresh_project(force=True)

    def _populate_conversations(self, project) -> None:
        self._populating_conversations = True
        self.conversations.clear()
        if project is None:
            self.conversations.addItem("Save a document to begin")
        else:
            catalog = project.catalog()
            for item in catalog:
                self.conversations.addItem(str(item["title"]), item["id"])
            active_id = project.manifest().get("active_conversation_id")
            index = self.conversations.findData(active_id, USER_ROLE)
            if index >= 0:
                self.conversations.setCurrentIndex(index)
        self._populating_conversations = False

    def _render_conversation(self, project) -> None:
        self.transcript.clear()
        if project is None:
            self.transcript.setPlainText(
                f"Save the active FreeCAD document, then ask {SHORT_NAME} to "
                "inspect or edit it."
            )
            return
        for turn in project.active_conversation().get("turns", []):
            role = "You" if turn.get("role") == "user" else SHORT_NAME
            self._append_message(role, str(turn.get("content", "")))

    @Slot(int)
    def _activate_conversation(self, index: int) -> None:
        if (
            self._populating_conversations
            or index < 0
            or self._provider_operation_active()
        ):
            return
        project = self.application.session.project_for_active_document()
        conversation_id = self.conversations.itemData(index, USER_ROLE)
        if project is None or not conversation_id:
            return
        try:
            project.activate_conversation(str(conversation_id))
            self._render_conversation(project)
            self.refresh_memory(project)
        except Exception as exc:
            self._show_error(str(exc))

    def _new_conversation(self) -> None:
        if self._provider_operation_active():
            return
        project = self.application.session.project_for_active_document()
        if project is None:
            self._show_error("Save the active FreeCAD document before starting a task")
            return
        try:
            project.create_conversation()
            self.refresh_project(force=True)
            self.prompt.setFocus()
        except Exception as exc:
            self._show_error(str(exc))

    def _open_settings(self) -> None:
        if self._provider_operation_active():
            return
        dialog = SettingsDialog(self.application.settings, self)
        if exec_dialog(dialog):
            self.application.reload_settings()
            self._refresh_provider_choices()
            self.refresh_provider_status()

    def _send_prompt(self) -> None:
        if self._provider_operation_active():
            return
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            self.prompt.setFocus()
            return
        project = self.application.session.project_for_active_document()
        if project is None:
            self._show_error(
                f"Save the active FreeCAD document before starting {SHORT_NAME}"
            )
            return

        self.prompt.clear()
        self._append_message("You", prompt)
        self.transcript.append(f"<p><b>{SHORT_NAME}</b></p>")
        self._received_delta = False
        self._turn_project = project
        self._deferred_project_refresh = False
        self._set_running(True)
        self.activity.setText("Starting Codex")

        thread = QtCore.QThread(self)
        worker = _TurnWorker(
            self.application.session,
            prompt,
            self.dispatcher,
            str(project.document_path),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.event.connect(self._handle_event)
        worker.completed.connect(self._turn_completed)
        worker.failed.connect(self._turn_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._turn_thread_finished)
        self._turn_thread = thread
        self._turn_worker = worker
        thread.start()

    @Slot(object)
    def _handle_event(self, event) -> None:
        if event.kind == "text_delta":
            self._received_delta = True
            cursor = self.transcript.textCursor()
            cursor.movePosition(CURSOR_END)
            cursor.insertText(event.message)
            self.transcript.setTextCursor(cursor)
            self.transcript.ensureCursorVisible()
            return
        labels = {
            "turn_started": event.message or "Codex is working",
            "activity": event.message or "Working",
            "tool_started": f"Using {event.message}",
            "tool_completed": f"Finished {event.message}",
            "provider_warning": event.message or "Provider warning",
            "turn_stopping": event.message or "Stopping",
        }
        if event.kind in labels:
            self.activity.setText(labels[event.kind])

    @Slot(object)
    def _turn_completed(self, result) -> None:
        if not self._received_delta:
            cursor = self.transcript.textCursor()
            cursor.movePosition(CURSOR_END)
            cursor.insertText(result.text)
            self.transcript.setTextCursor(cursor)
        self.transcript.append("")
        if result.status in {"interrupted", "cancelled", "canceled"}:
            self.activity.setText("Task stopped")
        else:
            count = int(result.tool_calls)
            suffix = f" with {count} tool call" if count == 1 else f" with {count} tool calls"
            self.activity.setText(f"Task complete{suffix}")
        self.refresh_memory(self._turn_project)
        self._refresh_conversation_titles(self._turn_project)

    @Slot(str)
    def _turn_failed(self, message: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(CURSOR_END)
        cursor.insertText(f"{SHORT_NAME} could not complete the task: {message}")
        self.transcript.setTextCursor(cursor)
        self.transcript.append("")
        self.activity.setText("Task failed")

    @Slot()
    def _turn_thread_finished(self) -> None:
        self._turn_thread = None
        self._turn_worker = None
        active_project = self.application.session.project_for_active_document()
        active_path = str(active_project.document_path) if active_project is not None else ""
        refresh_project = self._deferred_project_refresh or active_path != self._project_path
        self._turn_project = None
        self._deferred_project_refresh = False
        if refresh_project:
            self.refresh_project(force=True)
        self._set_running(False)
        if not refresh_project:
            self._refresh_conversation_titles(active_project)

    def _refresh_conversation_titles(self, project=None) -> None:
        project = project or self.application.session.project_for_active_document()
        self._populate_conversations(project)

    def request_stop(self) -> None:
        worker = self._turn_worker
        if worker is None:
            return
        worker.cancel_event.set()
        self.stop.setEnabled(False)
        self.activity.setText("Stopping after the current step")

    def _set_running(self, running: bool) -> None:
        has_project = self.application.session.project_for_active_document() is not None
        provider_busy = (
            running
            or self._login_thread is not None
            or self._status_thread is not None
        )
        idle_with_project = not provider_busy and has_project
        self.progress.setVisible(running)
        self.stop.setEnabled(running)
        self.send.setEnabled(idle_with_project)
        self.prompt.setEnabled(idle_with_project)
        self.conversations.setEnabled(idle_with_project)
        self.new_task.setEnabled(idle_with_project)
        self.providers.setEnabled(not provider_busy)
        self.settings_button.setEnabled(not provider_busy)
        self.connect_provider.setEnabled(not provider_busy)
        self.refresh_status.setEnabled(not provider_busy)

    def _provider_operation_active(self) -> bool:
        return any(
            worker is not None
            for worker in (
                self._turn_worker,
                self._login_thread,
                self._status_thread,
            )
        )

    def _append_message(self, role: str, content: str) -> None:
        safe_role = html.escape(role)
        safe_content = html.escape(content).replace("\n", "<br>")
        self.transcript.append(f"<p><b>{safe_role}</b><br>{safe_content}</p>")

    def _show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.warning(self, SHORT_NAME, message)
