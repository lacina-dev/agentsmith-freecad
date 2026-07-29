import json
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import time

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

# Pure logic split out of this file so it can be unit-tested without FreeCAD/Qt
# (see tests/). These modules never import FreeCAD; this file stays the only
# place that touches the live document and the GUI.
import agentsmith_backends
import agentsmith_harness
from agentsmith_harness import HARNESS_DIR
import agentsmith_lessons
import agentsmith_review
import agentsmith_supervision
import agentsmith_voice
import printer_book
from printer_panel import PrinterListWidget
from task_supervisor import LIVE_EDIT_BUDGET_SECONDS, TaskSupervisionMixin
# The live-document half of the addon. The panel drives it; it knows nothing
# about the panel.
from bridge_server import (BRIDGE_VERSION, DEFAULT_PORT, DISCOVERY_FILE,
                           BridgeServer, _atomic_write_bytes,
                           _camera_apply, _camera_snapshot, _document,
                           _document_fingerprint, _fcstd_archive_valid,
                           _optional_document, _rss_bytes, _validate_document)

try:
    from PySide import QtWidgets
except ImportError:  # FreeCAD versions exposing widgets through QtGui
    QtWidgets = QtGui


# Orientations that can be captured as reference snapshots, in a stable order.
CAPTURE_ORIENTATIONS = ("top", "bottom", "front", "rear", "left", "right")
# Model each backend starts on when the user has not picked one yet. Modeling is a
# long, expensive, hard-to-verify task, so the default is the strongest general
# model rather than whatever the CLI would pick.
DEFAULT_MODELS = {"claude": "claude-opus-5"}
_panel = None


class TranscribeWorker(QtCore.QThread):
    """Run whisper.cpp off the GUI thread; a minute of audio takes seconds."""

    done = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, wav_path, parent=None):
        super(TranscribeWorker, self).__init__(parent)
        self.wav_path = wav_path

    def run(self):
        try:
            self.done.emit(agentsmith_voice.transcribe(self.wav_path))
        except agentsmith_voice.VoiceUnavailable as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit("Transcription failed: %s" % exc)
        finally:
            try:
                os.unlink(self.wav_path)     # the audio is not kept around
            except OSError:
                pass


class ModelDownloadWorker(QtCore.QThread):
    """Fetch a whisper model, only ever after an explicit click."""

    progressed = QtCore.Signal(int, int)
    done = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, name, target_dir, parent=None):
        super(ModelDownloadWorker, self).__init__(parent)
        self.name = name
        self.target_dir = target_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        import urllib.request
        target = os.path.join(self.target_dir, self.name)
        partial = target + ".part"
        try:
            os.makedirs(self.target_dir, exist_ok=True)
            with urllib.request.urlopen(
                    agentsmith_voice.model_url(self.name), timeout=30) as response:
                total = int(response.headers.get("Content-Length") or 0)
                got = 0
                with open(partial, "wb") as handle:
                    while not self._cancelled:
                        chunk = response.read(262144)
                        if not chunk:
                            break
                        handle.write(chunk)
                        got += len(chunk)
                        self.progressed.emit(got, total)
            if self._cancelled:
                os.unlink(partial)
                self.failed.emit("Download cancelled.")
                return
            # Renamed only once complete: a half-downloaded model that looks
            # installed would fail later with a confusing whisper.cpp error.
            os.replace(partial, target)
            self.done.emit(target)
        except Exception as exc:
            try:
                os.unlink(partial)
            except OSError:
                pass
            self.failed.emit(str(exc))


class BridgePanel(TaskSupervisionMixin, QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = BridgeServer(self)
        self.server.log_message.connect(self._log)
        self.server.state_changed.connect(self._state)
        self.server.safety_violation.connect(self._task_safety_violation)

        self.status = QtWidgets.QLabel("Stopped")
        self.port = QtWidgets.QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(DEFAULT_PORT)
        self.toggle = QtWidgets.QPushButton("Start bridge")
        self.toggle.clicked.connect(self._toggle)
        self.python_box = QtWidgets.QCheckBox("Allow authenticated Python execution")
        self.python_box.setToolTip("Powerful mode. Enable only while actively collaborating; localhost and session token are still required.")
        self.python_box.toggled.connect(self._python_changed)
        self.access = QtWidgets.QComboBox()
        self.access.addItems(["Read only", "Edit", "Edit + Python"])
        self.access.setCurrentIndex(1)
        self.access.currentIndexChanged.connect(self._access_changed)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)

        self.port.setMaximumWidth(90)
        self.toggle.setMaximumWidth(110)
        bridge_group = QtWidgets.QGroupBox("FreeCAD bridge")
        bridge_layout = QtWidgets.QVBoxLayout(bridge_group)
        bridge_header = QtWidgets.QHBoxLayout()
        bridge_header.addWidget(self.status, 1)
        bridge_header.addWidget(QtWidgets.QLabel("Port"))
        bridge_header.addWidget(self.port)
        bridge_header.addWidget(self.access)
        bridge_header.addWidget(self.toggle)
        bridge_layout.addLayout(bridge_header)

        actions = QtWidgets.QHBoxLayout()
        checkpoint = QtWidgets.QPushButton("Checkpoint")
        checkpoint.clicked.connect(self._checkpoint)
        validate = QtWidgets.QPushButton("Validate")
        validate.clicked.connect(self._validate)
        undo = QtWidgets.QPushButton("Undo")
        undo.clicked.connect(self._undo)
        redo = QtWidgets.QPushButton("Redo")
        redo.clicked.connect(self._redo)
        for button in (checkpoint, validate, undo, redo):
            actions.addWidget(button)
        bridge_layout.addLayout(actions)
        reload_button = QtWidgets.QPushButton("Reload updated bridge")
        reload_button.clicked.connect(self._reload_bridge)
        actions.addWidget(reload_button)
        bridge_layout.addWidget(self.python_box)
        self.bridge_activity_label = QtWidgets.QLabel("Bridge activity")
        bridge_layout.addWidget(self.bridge_activity_label)
        self.log.setMaximumHeight(90)
        bridge_layout.addWidget(self.log)
        self.bridge_details = QtWidgets.QToolButton()
        self.bridge_details.setText("Show technical details")
        self.bridge_details.setCheckable(True)
        self.bridge_details.toggled.connect(self._toggle_bridge_details)
        bridge_layout.addWidget(self.bridge_details)
        self.python_box.hide()
        self.bridge_activity_label.hide()
        self.log.hide()

        self.codex_output = QtWidgets.QPlainTextEdit()
        self.codex_output.setReadOnly(True)
        self.codex_output.setMaximumBlockCount(1000)
        self.codex_prompt = QtWidgets.QPlainTextEdit()
        self.codex_prompt.setPlaceholderText("Describe what the assistant should do in the open FreeCAD model…")
        self.codex_prompt.setMaximumHeight(110)

        # Dictation. The transcript lands in the box above and is NEVER sent on
        # its own: a misheard dimension would otherwise launch an autonomous run
        # against the live document with nobody having read the sentence.
        self.dictate_button = QtWidgets.QPushButton("🎤 Dictate")
        self.dictate_button.setToolTip(
            "Records the microphone and transcribes it locally (whisper.cpp).\n"
            "The audio never leaves this machine. The transcript lands in the box — you press Send.")
        self.dictate_button.clicked.connect(self._toggle_dictation)
        self.dictate_status = QtWidgets.QLabel("")
        self.dictate_status.setStyleSheet("color: #888;")
        self.recorder_process = None
        self.recording_path = None
        self.transcribe_worker = None
        self.model_worker = None
        self.recording_blink = QtCore.QTimer(self)
        self.recording_blink.setInterval(500)
        self.recording_blink.timeout.connect(self._blink_recording)
        self._blink_on = False

        dictate_row = QtWidgets.QHBoxLayout()
        dictate_row.addWidget(self.dictate_button)
        dictate_row.addWidget(self.dictate_status, 1)
        self.reference_input = QtWidgets.QPlainTextEdit()
        self.reference_input.setPlaceholderText("References (optional): image paths or URLs, one per line…")
        self.reference_input.setMaximumHeight(54)
        self.backend = QtWidgets.QComboBox()
        self.model = QtWidgets.QComboBox()
        self.preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentSmith")
        try:
            # One-time migration from the old "CodexBridge" preferences node.
            if not self.preferences.GetString("Backend", ""):
                legacy_prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/CodexBridge")
                for legacy_key in ("Backend", "CaptureViews", "TaskBudgetSeconds", "ReviewerPass",
                                   "ReviewerAutofix", "Model_codex", "Model_claude", "Model_copilot"):
                    legacy_value = legacy_prefs.GetString(legacy_key, "")
                    if legacy_value:
                        self.preferences.SetString(legacy_key, legacy_value)
        except Exception:
            pass
        self.backends = self._detect_backends()
        for backend_id, backend in self.backends.items():
            label = "%s  ·  %s" % (backend["label"], backend["version"] or "not found")
            self.backend.addItem(label, backend_id)
            if not backend["available"]:
                self.backend.model().item(self.backend.count() - 1).setEnabled(False)
        saved_backend = self.preferences.GetString("Backend", "codex")
        saved_index = self.backend.findData(saved_backend)
        if saved_index >= 0 and self.backend.model().item(saved_index).isEnabled():
            self.backend.setCurrentIndex(saved_index)
        self.backend.currentIndexChanged.connect(self._backend_changed)
        self.model.currentIndexChanged.connect(self._model_changed)
        self.budget_choice = QtWidgets.QComboBox()
        self.budget_choice.setToolTip("Time budget for the task. Once it runs out the watchdog kills the backend and rolls the document back.")
        for budget_label, budget_seconds in (
            ("Quick edit (8 min)", 480),
            ("New part (15 min)", 900),
            ("Assembly (25 min)", 1500),
            ("Complex part (30 min)", 1800),
            ("Large assembly (45 min)", 2700),
            ("Marathon (60 min)", 3600),
        ):
            self.budget_choice.addItem(budget_label, budget_seconds)
        saved_budget = self.preferences.GetString("TaskBudgetSeconds", str(LIVE_EDIT_BUDGET_SECONDS))
        budget_index = self.budget_choice.findData(int(saved_budget)) if str(saved_budget).isdigit() else -1
        if budget_index >= 0:
            self.budget_choice.setCurrentIndex(budget_index)
        self.budget_choice.currentIndexChanged.connect(self._persist_budget)
        self.reviewer_enabled = QtWidgets.QCheckBox("Reviewer")
        self.reviewer_enabled.setToolTip(
            "After a successful task, runs an independent read-only verification pass: a second agent "
            "compares the result against the brief and the references and prints a verdict "
            "(advisory; it never changes the document).")
        self.reviewer_enabled.setChecked(self.preferences.GetString("ReviewerPass", "0") == "1")
        self.reviewer_enabled.toggled.connect(self._persist_reviewer)
        self.autofix_enabled = QtWidgets.QCheckBox("Auto-fix")
        self.autofix_enabled.setToolTip(
            "When the reviewer ends with a CONCERNS verdict, ONE fix-up round of the worker is "
            "started automatically with the reviewer findings in the brief. A fix-up round never "
            "triggers another auto-fix.")
        self.autofix_enabled.setChecked(self.preferences.GetString("ReviewerAutofix", "0") == "1")
        self.autofix_enabled.toggled.connect(self._persist_autofix)
        self.autofix_rounds = QtWidgets.QComboBox()
        self.autofix_rounds.setToolTip(
            "Maximum number of fix-up rounds. The loop usually stops earlier on its own — "
            "once the reviewer starts repeating the same findings, another round is just burnt "
            "budget. This is the backstop for the case where every round finds something new.")
        for rounds in (1, 2, 3, 5):
            self.autofix_rounds.addItem("max %d round(s)" % rounds, rounds)
        saved_rounds = self.preferences.GetString("AutofixMaxRounds", "2")
        rounds_index = self.autofix_rounds.findData(int(saved_rounds)) if str(saved_rounds).isdigit() else -1
        self.autofix_rounds.setCurrentIndex(rounds_index if rounds_index >= 0 else 1)
        self.autofix_rounds.currentIndexChanged.connect(self._persist_autofix_rounds)
        self.escalate_model = QtWidgets.QComboBox()
        self.escalate_model.setToolTip(
            "Model for the fix-up round. A model that already failed on this part gets "
            "reinforcements rather than another try. Empty = the same model.")
        self.escalate_model.currentIndexChanged.connect(self._persist_escalate_model)
        self.mcp_enabled = QtWidgets.QCheckBox("MCP")
        self.mcp_enabled.setToolTip(
            "Gives the backend the bridge as MCP tools with typed schemas instead of shelling "
            "out to freecad_bridge_client.py. No quoting, no output parsing, and the backend "
            "cannot invent a command that does not exist. Off by default: turn it on once it "
            "measures better than the baseline (see PLAN.md, phase 4).")
        self.mcp_enabled.setChecked(self.preferences.GetString("UseMcp", "0") == "1")
        self.mcp_enabled.toggled.connect(self._persist_mcp)
        self.view_current = QtWidgets.QCheckBox("Current camera")
        self.view_current.setToolTip("Capture a reference snapshot from the current camera position (default).")
        self.view_orientation_boxes = {}
        for view_id in CAPTURE_ORIENTATIONS:
            box = QtWidgets.QCheckBox(view_id.capitalize())
            box.toggled.connect(self._persist_capture_views)
            self.view_orientation_boxes[view_id] = box
        self.view_current.toggled.connect(self._persist_capture_views)
        saved_views = set(filter(None, self.preferences.GetString("CaptureViews", "current").split(",")))
        self.view_current.setChecked("current" in saved_views or not saved_views)
        for view_id, box in self.view_orientation_boxes.items():
            box.setChecked(view_id in saved_views)
        self.codex_full_access = QtWidgets.QCheckBox("Allow the backend full local access for work on the live model")
        self.codex_full_access.setToolTip("Required for access to the localhost bridge. Enable only for trusted modeling tasks.")
        self.codex_full_access.setChecked(True)
        self.codex_full_access.hide()
        self.codex_supervisor_status = QtWidgets.QLabel("Supervisor: idle")
        self.codex_supervisor_status.setWordWrap(True)
        self.task_status = QtWidgets.QLabel("Ready")
        self.task_status.setAlignment(QtCore.Qt.AlignCenter)
        self.task_elapsed = QtWidgets.QLabel("")
        self.task_progress = QtWidgets.QProgressBar()
        self.task_progress.setRange(0, 0)
        self.task_progress.setTextVisible(False)
        self.task_progress.setMaximumHeight(8)
        self.task_progress.hide()
        self.task_timer = QtCore.QTimer(self)
        self.task_timer.setInterval(1000)
        self.task_timer.timeout.connect(self._update_task_elapsed)
        self.history_timer = QtCore.QTimer(self)
        self.history_timer.setInterval(1000)
        self.history_timer.timeout.connect(self._refresh_document_history)
        self.file_guard_timer = QtCore.QTimer(self)
        self.file_guard_timer.setInterval(500)
        self.file_guard_timer.timeout.connect(self._check_task_guard)
        self.task_started_monotonic = None
        self.task_stop_requested = False
        self._set_task_state("idle", "Ready")
        self.codex_run = QtWidgets.QPushButton("Send")
        self.codex_run.clicked.connect(self._run_codex)
        self.codex_stop = QtWidgets.QPushButton("Stop")
        self.codex_stop.setEnabled(False)
        self.codex_stop.clicked.connect(self._stop_codex)
        self.history_choice = QtWidgets.QComboBox()
        self.history_choice.setMinimumWidth(220)
        self.history_restore = QtWidgets.QPushButton("Roll back to before this step")
        self.history_restore.clicked.connect(self._restore_history_step)
        self.lesson_button = QtWidgets.QPushButton("Record lesson")
        self.lesson_button.setToolTip(
            "Drafts one entry for harness/lessons.md out of the last failure or CONCERNS "
            "verdict. You see the draft and confirm it — nothing is written without "
            "confirmation, because the modeling agent treats lessons.md as binding.")
        self.lesson_button.setEnabled(False)
        self.lesson_button.clicked.connect(self._draft_lesson)
        self._lesson_source = None
        self.history_label = QtWidgets.QLabel("History: no saved steps")
        history_row = QtWidgets.QHBoxLayout()
        history_row.addWidget(self.history_label)
        history_row.addWidget(self.history_choice, 1)
        history_row.addWidget(self.history_restore)
        history_row.addWidget(self.lesson_button)
        codex_buttons = QtWidgets.QHBoxLayout()
        codex_buttons.addWidget(self.codex_run)
        codex_buttons.addWidget(self.codex_stop)
        chat_group = QtWidgets.QGroupBox("Chat")
        codex_layout = QtWidgets.QVBoxLayout(chat_group)
        backend_row = QtWidgets.QHBoxLayout()
        backend_row.addWidget(QtWidgets.QLabel("Backend"))
        backend_row.addWidget(self.backend, 1)
        backend_row.addWidget(QtWidgets.QLabel("Model"))
        backend_row.addWidget(self.model, 1)
        codex_layout.addLayout(backend_row)
        task_row = QtWidgets.QHBoxLayout()
        task_row.addWidget(self.task_status)
        task_row.addWidget(self.task_elapsed)
        task_row.addStretch(1)
        codex_layout.addLayout(task_row)
        codex_layout.addWidget(self.task_progress)
        codex_layout.addWidget(self.codex_output, 1)
        codex_layout.addWidget(self.codex_prompt)
        codex_layout.addLayout(dictate_row)
        reference_row = QtWidgets.QHBoxLayout()
        reference_row.addWidget(QtWidgets.QLabel("Reference"))
        reference_row.addWidget(self.reference_input, 1)
        reference_row.addWidget(QtWidgets.QLabel("Budget"))
        reference_row.addWidget(self.budget_choice)
        reference_row.addWidget(self.reviewer_enabled)
        reference_row.addWidget(self.autofix_enabled)
        reference_row.addWidget(self.autofix_rounds)
        reference_row.addWidget(self.escalate_model)
        reference_row.addWidget(self.mcp_enabled)
        codex_layout.addLayout(reference_row)
        views_row = QtWidgets.QHBoxLayout()
        views_row.addWidget(QtWidgets.QLabel("Snapshots:"))
        views_row.addWidget(self.view_current)
        for view_id in CAPTURE_ORIENTATIONS:
            views_row.addWidget(self.view_orientation_boxes[view_id])
        views_row.addStretch(1)
        codex_layout.addLayout(views_row)
        codex_layout.addWidget(self.codex_supervisor_status)
        codex_layout.addLayout(history_row)
        codex_layout.addLayout(codex_buttons)

        self.codex_process = QtCore.QProcess(self)
        self.codex_process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.codex_process.readyReadStandardOutput.connect(self._codex_output_ready)
        self.codex_process.finished.connect(self._codex_finished)
        self.codex_process.errorOccurred.connect(self._codex_process_error)
        # Independent, advisory read-only "reviewer" pass (judge != defendant). It runs
        # AFTER a task succeeds, never touches the protected document (bridge forced to
        # read-only for its duration), and only appends a verdict to the chat/history.
        self.reviewer_process = QtCore.QProcess(self)
        self.reviewer_process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.reviewer_process.readyReadStandardOutput.connect(self._reviewer_output_ready)
        self.reviewer_process.finished.connect(self._reviewer_finished)
        self.reviewer_buffer = ""
        self.reviewer_state = None
        self.reviewer_run_id = 0
        self.codex_buffer = ""
        self.codex_task = None
        self.chat_seen = set()
        self.loaded_history_path = None
        self.active_document_path = "__unset__"

        self.printers = PrinterListWidget()

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(chat_group, 1)
        layout.addWidget(self.printers, 0)
        layout.addWidget(bridge_group, 0)
        self._backend_changed()
        self._refresh_document_history(True, True)
        self.history_timer.start()

    # ---------------------------------------------------------------- dictation
    def _toggle_dictation(self):
        if self.recorder_process is not None:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        import tempfile
        report = agentsmith_voice.availability()
        if not report["ready"]:
            self._offer_voice_setup(report)
            return
        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        handle.close()
        self.recording_path = handle.name
        try:
            args = agentsmith_voice.record_args(report["recorder"], self.recording_path)
        except agentsmith_voice.VoiceUnavailable as exc:
            self.dictate_status.setText(str(exc))
            return
        self.recorder_process = QtCore.QProcess(self)
        self.recorder_process.start(args[0], args[1:])
        if not self.recorder_process.waitForStarted(3000):
            self.dictate_status.setText("Recording did not start (%s)." % args[0])
            self.recorder_process = None
            return
        self.dictate_button.setText("⏹ Stop")
        self.recording_blink.start()
        self._log("Dictation: recording via %s" % os.path.basename(args[0]))

    def _blink_recording(self):
        # A thing that can listen must visibly show that it is listening.
        self._blink_on = not self._blink_on
        self.dictate_status.setText("● recording…" if self._blink_on else "  recording…")
        self.dictate_status.setStyleSheet("color: #c62828; font-weight: bold;")

    def _stop_recording(self):
        process, self.recorder_process = self.recorder_process, None
        self.recording_blink.stop()
        self.dictate_button.setText("🎤 Dictate")
        self.dictate_button.setEnabled(False)
        self.dictate_status.setStyleSheet("color: #888;")
        self.dictate_status.setText("transcribing…")
        if process is not None:
            process.terminate()
            if not process.waitForFinished(4000):
                process.kill()
                process.waitForFinished(2000)
        self.transcribe_worker = TranscribeWorker(self.recording_path, self)
        self.transcribe_worker.done.connect(self._dictation_done)
        self.transcribe_worker.failed.connect(self._dictation_failed)
        self.transcribe_worker.start()

    def _dictation_done(self, text):
        self.dictate_button.setEnabled(True)
        if not text:
            self.dictate_status.setText("nothing was understood")
            return
        # Appended to whatever is already typed, and left for the user to read.
        # Sending it automatically would let a misheard dimension start a run.
        existing = self.codex_prompt.toPlainText()
        self.codex_prompt.setPlainText((existing + " " + text).strip()
                                       if existing.strip() else text)
        self.codex_prompt.setFocus()
        cursor = self.codex_prompt.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.codex_prompt.setTextCursor(cursor)
        self.dictate_status.setText("transcribed — check it and send")

    def _dictation_failed(self, message):
        self.dictate_button.setEnabled(True)
        self.dictate_status.setText(message)
        self._log("Dictation failed: %s" % message)

    def _offer_voice_setup(self, report):
        """Explain what is missing, and offer the one step we can take here."""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Voice input is not set up")
        box.setText("\n".join(report["problems"]))
        box.setDetailedText(agentsmith_voice.install_hint())
        download = None
        if report["binary"] and not report["model"]:
            download = box.addButton("Download a model…", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Close", QtWidgets.QMessageBox.RejectRole)
        box.exec_()
        if download is not None and box.clickedButton() is download:
            self._download_model()

    def _download_model(self):
        choices = ["%s — %d MB (%s)" % (label, size, note)
                   for _name, label, size, note in agentsmith_voice.MODELS]
        choice, ok = QtWidgets.QInputDialog.getItem(
            self, "Download a model", "For Czech the first one is recommended:", choices, 0, False)
        if not ok:
            return
        name = agentsmith_voice.MODELS[choices.index(choice)][0]
        self.model_worker = ModelDownloadWorker(name, agentsmith_voice.MODEL_DIR, self)
        self.model_worker.progressed.connect(
            lambda got, total: self.dictate_status.setText(
                "downloading the model… %d %%" % (100 * got // total) if total
                else "downloading the model… %d MB" % (got // 1048576)))
        self.model_worker.done.connect(
            lambda path: self.dictate_status.setText("model downloaded — dictation is ready"))
        self.model_worker.failed.connect(
            lambda message: self.dictate_status.setText("download failed: %s" % message))
        self.model_worker.start()

    def _toggle(self):
        if self.server.server.isListening():
            self.server.stop()
        else:
            try:
                self.server.start(self.port.value())
            except Exception as exc:
                self._log("ERROR: %s" % exc)

    def _toggle_bridge_details(self, visible):
        self.python_box.setVisible(visible)
        self.bridge_activity_label.setVisible(visible)
        self.log.setVisible(visible)
        self.bridge_details.setText("Hide technical details" if visible else "Show technical details")

    def _python_changed(self, enabled):
        self.server.allow_python = enabled
        if enabled and self.access.currentIndex() < 2:
            self.access.setCurrentIndex(2)
        self._log("Python execution %s" % ("enabled" if enabled else "disabled"))

    def _access_changed(self, index):
        self.server.access_level = "read" if index == 0 else "edit"
        self.server.allow_python = index == 2
        self.python_box.blockSignals(True)
        self.python_box.setChecked(index == 2)
        self.python_box.blockSignals(False)
        self._log("Access level: %s" % self.access.currentText())

    def _state(self, running, port):
        self.status.setText("Running on 127.0.0.1:%d" % port if running else "Stopped")
        self.toggle.setText("Stop bridge" if running else "Start bridge")
        self.port.setEnabled(not running)

    def _log(self, message):
        self.log.appendPlainText(message)

    def _checkpoint(self):
        try:
            result = self.server._checkpoint({"label": "manual"})
            self._log("Checkpoint: %s" % result["path"])
        except Exception as exc:
            self._log("ERROR: %s" % exc)

    def _validate(self):
        try:
            report = _validate_document(_document())
            self._log("Validation: %s (%d errors, %d warnings)" % ("OK" if report["ok"] else "FAILED", len(report["errors"]), len(report["warnings"])))
        except Exception as exc:
            self._log("ERROR: %s" % exc)

    def _undo(self):
        try:
            _document().undo()
            _document().recompute()
            self._log("Undo")
        except Exception as exc:
            self._log("ERROR: %s" % exc)

    def _redo(self):
        try:
            _document().redo()
            _document().recompute()
            self._log("Redo")
        except Exception as exc:
            self._log("ERROR: %s" % exc)

    def _reload_bridge(self):
        self._log("Reloading AgentSmith…")
        self._stop_codex()
        self.server.stop()
        dock = Gui.getMainWindow().findChild(QtWidgets.QDockWidget, "AgentSmithDock")
        if dock is not None:
            dock.hide()
            dock.setWidget(QtWidgets.QWidget())
            dock.deleteLater()
        module = __import__(__name__)
        import importlib
        importlib.reload(module)
        QtCore.QTimer.singleShot(100, module.show_panel)

    def _project_directory(self):
        doc = App.ActiveDocument
        return os.path.dirname(doc.FileName) if doc and doc.FileName else os.getcwd()

    def _harness_registry(self):
        """Load and cache the modeling-harness registry. Robust to a missing/broken file."""
        if getattr(self, "_harness_registry_cache", None) is not None:
            return self._harness_registry_cache
        registry = agentsmith_harness.load_registry(HARNESS_DIR, warn=self._log)
        self._harness_registry_cache = registry
        return registry

    def _harness_text(self):
        """Assemble the full modeling harness: core methodology + the whole playbook
        library, framed so the backend applies each playbook conditionally.

        There is no exclusive mode: every playbook is always provided. A generated index
        lists each playbook with its trigger so the agent applies those whose trigger
        matches the part's character/use and ignores the rest.
        """
        return agentsmith_harness.assemble_harness(
            HARNESS_DIR, warn=self._log, registry=self._harness_registry())

    def _selected_capture_views(self):
        """Ordered list of reference views to capture, honoring the UI checkboxes."""
        views = []
        if self.view_current.isChecked():
            views.append("current")
        for view_id in CAPTURE_ORIENTATIONS:
            box = self.view_orientation_boxes.get(view_id)
            if box is not None and box.isChecked():
                views.append(view_id)
        if not views:
            views.append("current")
        return views

    def _persist_capture_views(self, *_):
        try:
            self.preferences.SetString("CaptureViews", ",".join(self._selected_capture_views()))
        except Exception:
            pass

    def _persist_autofix_rounds(self, *_):
        try:
            self.preferences.SetString("AutofixMaxRounds",
                                       str(self.autofix_rounds.currentData() or 1))
        except Exception:
            pass

    def _persist_escalate_model(self, *_):
        try:
            self.preferences.SetString("EscalateModel_" + str(self.backend.currentData() or ""),
                                       str(self.escalate_model.currentData() or ""))
        except Exception:
            pass

    def _persist_mcp(self, *_):
        try:
            self.preferences.SetString("UseMcp", "1" if self.mcp_enabled.isChecked() else "0")
        except Exception:
            pass

    def _mcp_settings(self, project):
        """Per-task MCP wiring, or None when the checkbox is off.

        The config is written into the project's own .agentsmith directory rather
        than registered globally: a modeling run must not leave a server behind in
        the user's CLI config, and two FreeCAD instances must not fight over one
        registration.
        """
        if not self.mcp_enabled.isChecked():
            return None
        server = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
        if not os.path.isfile(server):
            self._log("MCP requested but mcp_server.py is missing — using the bridge CLI client.")
            return None
        settings = {"name": agentsmith_backends.MCP_SERVER_NAME,
                    "command": agentsmith_backends.mcp_python(), "args": [server]}
        try:
            config_dir = os.path.join(project, ".agentsmith")
            os.makedirs(config_dir, exist_ok=True)
            config_path = os.path.join(config_dir, "mcp.json")
            document = agentsmith_backends.mcp_config_document(
                settings["command"], settings["args"], settings["name"])
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2)
            settings["config_path"] = config_path
        except Exception as exc:
            # claude needs the file; codex does not. Keep going so the backend that
            # can work without it still gets its tools.
            self._log("MCP config could not be written (%s)" % exc)
        return settings

    def _persist_reviewer(self, *_):
        try:
            self.preferences.SetString("ReviewerPass", "1" if self.reviewer_enabled.isChecked() else "0")
        except Exception:
            pass

    def _persist_autofix(self, *_):
        try:
            self.preferences.SetString("ReviewerAutofix", "1" if self.autofix_enabled.isChecked() else "0")
        except Exception:
            pass

    def _persist_budget(self, *_):
        try:
            self.preferences.SetString("TaskBudgetSeconds", str(int(self.budget_choice.currentData() or LIVE_EDIT_BUDGET_SECONDS)))
        except Exception:
            pass

    def _history_path(self, file_name=None):
        path = os.path.abspath(file_name or (App.ActiveDocument.FileName if App.ActiveDocument else ""))
        if not path:
            return None
        directory = os.path.join(os.path.dirname(path), ".freecad-history")
        return os.path.join(directory, os.path.basename(path) + ".jsonl")

    def _read_document_history(self, file_name=None):
        path = self._history_path(file_name)
        entries = []
        if path and not os.path.isfile(path):
            self._migrate_legacy_history(file_name, path)
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                        if isinstance(item, dict):
                            entries.append(item)
                    except Exception:
                        continue
        return entries

    def _migrate_legacy_history(self, file_name, destination):
        canonical = os.path.abspath(file_name or "")
        legacy = os.path.join(os.path.dirname(canonical), ".codex-freecad", "task-history.jsonl")
        if not canonical or not os.path.isfile(legacy):
            return
        migrated = []
        with open(legacy, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    old = json.loads(line)
                except Exception:
                    continue
                checkpoint = old.get("checkpoint", "")
                expected_dir = os.path.join(os.path.dirname(canonical), ".freecad-checkpoints", os.path.splitext(os.path.basename(canonical))[0])
                if checkpoint and os.path.commonpath([os.path.abspath(checkpoint), expected_dir]) == expected_dir:
                    old["checkpoint_before"] = old.get("checkpoint")
                    old["checkpoint_after"] = old.get("after_checkpoint")
                    old["canonical_path"] = canonical
                    old["migrated_from_legacy_audit"] = True
                    migrated.append(old)
        if migrated:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "w", encoding="utf-8") as handle:
                for item in migrated:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _append_document_history(self, file_name, entry):
        path = self._history_path(file_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        with open(path, "ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self.loaded_history_path = None

    def _effective_history(self, entries):
        effective = []
        for item in entries:
            if item.get("status") == "success":
                effective.append(item)
            elif item.get("status") == "restored":
                target = item.get("target_task_id")
                index = next((i for i, previous in enumerate(effective) if previous.get("task_id") == target), len(effective))
                effective = effective[:index]
                effective.append(item)
        return effective

    def _refresh_document_history(self, force=False, show_chat=False):
        doc = App.ActiveDocument
        path = self._history_path(doc.FileName) if doc and doc.FileName else None
        switched = path != self.active_document_path
        if switched and not self.codex_task:
            # The active FreeCAD document changed: chat + history follow the file.
            self.active_document_path = path
            force = True
            show_chat = True
            self.codex_supervisor_status.setText("Supervisor: idle")
            self._set_task_state("idle", "Ready")
            self.task_elapsed.setText("")
        if not force and path == self.loaded_history_path:
            return
        self.loaded_history_path = path
        entries = self._read_document_history(doc.FileName) if doc and doc.FileName else []
        effective = self._effective_history(entries)
        successful = [item for item in effective if item.get("status") == "success" and item.get("checkpoint_before")]
        blocker = QtCore.QSignalBlocker(self.history_choice)
        self.history_choice.clear()
        for item in reversed(successful):
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.get("finished", 0)))
            label = "%s · %s" % (stamp, str(item.get("prompt", ""))[:80])
            self.history_choice.addItem(label, item)
        del blocker
        self.history_label.setText("History: %d steps" % len(successful))
        self.history_choice.setEnabled(bool(successful) and not self.codex_task)
        self.history_restore.setEnabled(bool(successful) and not self.codex_task)
        if show_chat:
            self.codex_output.clear()
            if not doc:
                self.codex_output.appendPlainText("No document is open. Open or create an .FCStd model.")
            elif not (doc and doc.FileName):
                self.codex_output.appendPlainText("New unsaved document \u201c%s\u201d — no history yet. Save it as .FCStd and start chatting." % doc.Label)
            elif not entries:
                self.codex_output.appendPlainText("Document \u201c%s\u201d — no history yet. Write the first task." % doc.Label)
            else:
                self.codex_output.appendPlainText("Loaded the context of document \u201c%s\u201d: %d entries, %d successful changes.\n" % (doc.Label, len(entries), len(successful)))
                for item in entries[-8:]:
                    marker = "✓" if item.get("status") == "success" else "✗"
                    self.codex_output.appendPlainText("%s %s" % (marker, item.get("prompt", "no description")))

    def _history_context(self, file_name):
        entries = self._read_document_history(file_name)
        relevant = self._effective_history(entries)[-12:]
        finished_ids = {item.get("task_id") for item in entries if item.get("status") != "pending"}
        interrupted = [item for item in entries if item.get("status") == "pending" and item.get("task_id") not in finished_ids]
        diagnostics = [item for item in entries if item.get("status") in ("failed", "interrupted")][-4:]
        relevant.extend(interrupted[-2:] + diagnostics)
        relevant = sorted({item.get("task_id", "row-%d" % index): item for index, item in enumerate(relevant)}.values(), key=lambda item: item.get("started", item.get("finished", 0)))[-16:]
        reviews = [item for item in entries if item.get("status") == "review" and item.get("verdict")][-2:]
        if not relevant and not reviews:
            return "No persisted change history exists for this document yet."
        compact = []
        for item in relevant:
            compact.append({
                "time": item.get("finished"), "status": item.get("status"),
                "request": item.get("prompt"), "result": item.get("response", "")[-1200:],
                "changed_objects": item.get("changed_objects", []),
                "checkpoint_after": item.get("checkpoint_after"),
            })
        # The independent reviewer's latest findings carry forward: the next task should
        # know what an audit of this document already flagged or confirmed.
        for item in reviews:
            compact.append({
                "time": item.get("finished"), "status": "review",
                "reviewer_verdict": item.get("verdict", "")[-1500:],
            })
        return json.dumps(compact, ensure_ascii=False, indent=2)

    def _restore_history_step(self):
        item = self.history_choice.currentData()
        doc = App.ActiveDocument
        if not item or not doc or self.codex_task:
            return
        checkpoint = item.get("checkpoint_before")
        if not _fcstd_archive_valid(checkpoint):
            QtWidgets.QMessageBox.critical(self, "Checkpoint unavailable", "The saved point cannot be opened or is corrupt.")
            return
        canonical = os.path.abspath(doc.FileName)
        with open(checkpoint, "rb") as handle:
            data = handle.read()
        App.closeDocument(doc.Name)
        _atomic_write_bytes(canonical, data)
        restored = App.openDocument(canonical)
        restored.recompute()
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        record = {"status": "restored", "finished": time.time(), "prompt": "Rolled back to before: " + item.get("prompt", ""),
                  "restored_from": checkpoint, "target_task_id": item.get("task_id")}
        self._append_document_history(canonical, record)
        self._refresh_document_history(True, True)

    def _detect_backends(self):
        definitions = [
            ("codex", "Codex", "codex"),
            ("claude", "Claude Code", "claude"),
            ("copilot", "GitHub Copilot", "copilot"),
        ]
        detected = {}
        for backend_id, label, command in definitions:
            path = shutil.which(command)
            version = ""
            if path:
                try:
                    completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3)
                    version = (completed.stdout or completed.stderr).strip().splitlines()[0]
                except Exception:
                    version = "version unknown"
            detected[backend_id] = {"label": label, "command": path, "version": version, "available": bool(path)}
        return detected

    def _backend_changed(self, index=None):
        backend_id = self.backend.currentData()
        backend = self.backends.get(backend_id, {})
        self.preferences.SetString("Backend", backend_id or "codex")
        blocker = QtCore.QSignalBlocker(self.model)
        self.model.clear()
        for model_id, label in self._models_for_backend(backend_id):
            self.model.addItem(label, model_id)
        # No saved choice yet → the backend's recommended modeling model, not the
        # first entry ("Default / Auto"), which leaves the model up to the CLI.
        saved_model = self.preferences.GetString(
            "Model_" + str(backend_id), DEFAULT_MODELS.get(backend_id, ""))
        model_index = self.model.findData(saved_model)
        self.model.setCurrentIndex(model_index if model_index >= 0 else 0)
        del blocker

        escalate_blocker = QtCore.QSignalBlocker(self.escalate_model)
        self.escalate_model.clear()
        self.escalate_model.addItem("No escalation", "")
        for model_id, label in self._models_for_backend(backend_id):
            if model_id:
                self.escalate_model.addItem(label, model_id)
        saved_escalation = self.preferences.GetString("EscalateModel_" + str(backend_id), "")
        escalate_index = self.escalate_model.findData(saved_escalation)
        self.escalate_model.setCurrentIndex(escalate_index if escalate_index >= 0 else 0)
        del escalate_blocker

        self.codex_run.setText("Send via %s" % backend.get("label", "backend"))






    def _model_changed(self, index=None):
        backend_id = self.backend.currentData()
        if backend_id:
            self.preferences.SetString("Model_" + str(backend_id), str(self.model.currentData() or ""))

    def _models_for_backend(self, backend_id):
        models = [("", "Default / Auto")]
        try:
            if backend_id == "codex":
                home = App.ConfigGet("UserHomePath")
                cache_path = os.path.join(home, ".codex", "models_cache.json")
                catalog = None
                if os.path.isfile(cache_path):
                    with open(cache_path, "r", encoding="utf-8") as handle:
                        catalog = json.load(handle)
                if not catalog:
                    completed = subprocess.run([self.backends[backend_id]["command"], "debug", "models"], capture_output=True, text=True, timeout=8)
                    catalog = json.loads(completed.stdout)
                for item in catalog.get("models", []):
                    if item.get("visibility", "list") == "list" and item.get("slug"):
                        models.append((item["slug"], "%s  ·  %s" % (item.get("display_name", item["slug"]), item["slug"])))
            elif backend_id == "claude":
                # Explicit model IDs, not the moving aliases: a modeling task is a long,
                # expensive run and which generation produced a result matters when the
                # eval baseline is compared later. Ordered strongest-for-modeling first.
                models.extend([
                    ("claude-opus-5", "Opus 5 · default for modeling"),
                    ("claude-fable-5", "Fable 5 · the hardest tasks"),
                    ("claude-sonnet-5", "Sonnet 5 · faster and cheaper"),
                    ("claude-haiku-4-5", "Haiku 4.5 · trivial tasks only"),
                ])
            elif backend_id == "copilot":
                completed = subprocess.run([self.backends[backend_id]["command"], "help", "config"], capture_output=True, text=True, timeout=8)
                in_models = False
                for line in completed.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("`model`:"):
                        in_models = True
                        continue
                    if in_models and stripped.startswith("`contextTier`:"):
                        break
                    if in_models and stripped.startswith('- "') and stripped.endswith('"'):
                        model_id = stripped[3:-1]
                        models.append((model_id, model_id))
        except Exception as exc:
            self._log("Model catalog warning (%s): %s" % (backend_id, exc))
        return models





























    def closeEvent(self, event):
        if self.codex_task:
            task = self.codex_task
            self.file_guard_timer.stop()
            if self.codex_process.state() != QtCore.QProcess.NotRunning:
                self.codex_process.kill()
                self.codex_process.waitForFinished(1500)
            self._restore_task_snapshot(task, "Chat panel closed during an active task")
            self.server.protected_documents.pop(task["document"], None)
            self.server.supervised_metrics = None
            self.codex_task = None
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.reviewer_state = None
            self.reviewer_process.kill()
            self.reviewer_process.waitForFinished(1000)
        self.server.stop()
        super().closeEvent(event)


class ShowPanelCommand:
    def GetResources(self):
        return {"MenuText": "Show AgentSmith panel", "ToolTip": "Show the AgentSmith control panel"}

    def Activated(self):
        show_panel()

    def IsActive(self):
        return True


def show_panel():
    global _panel
    main = Gui.getMainWindow()
    dock = main.findChild(QtWidgets.QDockWidget, "AgentSmithDock")
    if dock is None:
        dock = QtWidgets.QDockWidget("AgentSmith", main)
        dock.setObjectName("AgentSmithDock")
        _panel = BridgePanel(dock)
        dock.setWidget(_panel)
        main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    dock.show()
    dock.raise_()
    return dock


def register_commands():
    Gui.addCommand("AgentSmith_ShowPanel", ShowPanelCommand())
