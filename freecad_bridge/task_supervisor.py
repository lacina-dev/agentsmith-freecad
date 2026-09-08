"""task_supervisor.py -- running one supervised modeling task, start to finish.

Everything between "the user pressed Send" and "the document is either changed
or rolled back": launching the backend, streaming its output, the watchdog that
enforces the budget and the mutation guards, checkpoint restore, the independent
reviewer pass, the auto-fix rounds, and turning a failure into a written lesson.

Split out of BridgeGui.py as a mixin rather than a separate object on purpose.
These methods are the panel -- they read its widgets and write its status labels
on every other line -- so handing them a reference to it would buy nothing but a
second name for `self`. What the split does buy is that the two things the file
used to mix are now separable when read: assembling the interface, and driving a
task through it.

The decisions themselves live further out, in the agentsmith_* modules that
import neither FreeCAD nor Qt (supervision verdicts, backend command lines,
review convergence, lesson formatting) and are covered by tests/.
"""

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:  # FreeCAD versions exposing widgets through QtGui
    QtWidgets = QtGui

import agentsmith_backends
import agentsmith_harness
from agentsmith_harness import HARNESS_DIR
import agentsmith_lessons
import agentsmith_review
import agentsmith_supervision
import printer_book
import printer_discovery
from bridge_server import (BRIDGE_VERSION, DISCOVERY_FILE, _atomic_write_bytes,
                           _camera_apply, _camera_snapshot, _document,
                           _document_fingerprint, _fcstd_archive_valid,
                           _optional_document, _rss_bytes, _validate_document)

# Hard wall-clock limit for a single live-editing task. The watchdog terminates
# the backend once this is exceeded. It is also surfaced to the backend so it can
# budget its time and act decisively instead of investigating until it is killed.
LIVE_EDIT_BUDGET_SECONDS = 900



class TaskSupervisionMixin(object):
    """Task execution for BridgePanel. Not usable on its own: every method here
    assumes the panel's widgets and state attributes exist."""

    def _set_task_state(self, state, text):
        colors = {
            "idle": ("#5f6368", "#eef0f2"),
            "running": ("#0b57d0", "#dbe8ff"),
            "success": ("#137333", "#d9f2e3"),
            "failed": ("#b3261e", "#f9dedc"),
            "stopped": ("#8a4b00", "#ffead1"),
        }
        foreground, background = colors.get(state, colors["idle"])
        self.task_status.setText(text)
        self.task_status.setStyleSheet("QLabel { color: %s; background: %s; border-radius: 8px; padding: 3px 10px; font-weight: bold; }" % (foreground, background))
        running = state == "running"
        self.task_progress.setVisible(running)
        if not running:
            self.task_timer.stop()
    def _update_task_elapsed(self):
        if self.task_started_monotonic is None:
            self.task_elapsed.setText("")
            return
        elapsed = max(0, int(time.monotonic() - self.task_started_monotonic))
        self.task_elapsed.setText("%d:%02d" % divmod(elapsed, 60))
    def _check_task_guard(self):
        task = self.codex_task
        if not task:
            self.file_guard_timer.stop()
            return
        canonical = task.get("canonical_path")
        try:
            self.server.ensure_discovery()
            discovery_error = None
        except Exception as exc:
            discovery_error = exc
        doc = _optional_document(task["document"])
        violation, missing_checks, restore_file = agentsmith_supervision.evaluate_guard(
            elapsed_seconds=time.time() - task.get("started", time.time()),
            budget_seconds=task.get("budget_seconds", LIVE_EDIT_BUDGET_SECONDS),
            rss_growth_bytes=max(0, _rss_bytes() - task.get("rss_before", 0)),
            missing_checks=task.get("missing_checks", 0),
            document_missing=doc is None,
            path_changed=doc is not None and os.path.abspath(doc.FileName or "") != canonical,
            canonical_missing=bool(canonical) and not os.path.isfile(canonical),
            discovery_error=discovery_error,
        )
        task["missing_checks"] = missing_checks
        if restore_file:
            _atomic_write_bytes(canonical, task["file_backup_bytes"])
        if violation and not task.get("guard_violation"):
            if agentsmith_supervision.is_budget_exhaustion(violation):
                if task.get("budget_exhausted"):
                    return
                # Time ran out, but nothing is wrong with the document. Stop the
                # backend and let _supervise_codex_result decide on the evidence:
                # a document that changed and validates is kept and saved, one
                # that does not is rolled back. A finished model must never be
                # discarded because the backend spent its last minutes writing
                # the report.
                task["budget_exhausted"] = violation
                self.codex_output.appendPlainText(
                    "\nTIME BUDGET: %s. Stopping the backend; the live document is kept "
                    "if it verifies, otherwise rolled back." % violation)
                self._set_task_state("running", "Budget exhausted, verifying")
            else:
                task["guard_violation"] = violation
                self.codex_output.appendPlainText("\nFILE GUARD: " + violation)
                self._set_task_state("failed", "File guard tripped")
            if self.codex_process.state() != QtCore.QProcess.NotRunning:
                self.codex_process.terminate()
    def _task_safety_violation(self, reason):
        task = self.codex_task
        if not task or task.get("guard_violation"):
            return
        task["guard_violation"] = reason
        self.codex_output.appendPlainText("\nSAFETY LIMIT: " + reason)
        self._set_task_state("failed", "Safety limit tripped")
        if self.codex_process.state() != QtCore.QProcess.NotRunning:
            self.codex_process.terminate()
    def _restore_task_snapshot(self, task, reason):
        canonical = task["canonical_path"]
        current = _optional_document(task["document"])
        if current is not None:
            # Whatever is about to be thrown away goes to a rescue checkpoint
            # first. A rollback is the supervisor's judgement that the result was
            # not verified — it is not a judgement that hours of the user's
            # geometry are worthless, and a checkpoint costs nothing.
            try:
                rescue = self.server._checkpoint({"document": current.Name,
                                                  "label": "task-rescue-" + task["id"]})["path"]
                task["rescue_checkpoint"] = rescue
                self.codex_output.appendPlainText("\nRESCUE: pre-rollback state saved to %s" % rescue)
            except Exception as exc:
                self.codex_output.appendPlainText("\nRESCUE: could not checkpoint the pre-rollback state: %s" % exc)
            App.closeDocument(current.Name)
        _atomic_write_bytes(canonical, task["file_backup_bytes"])
        restored = App.openDocument(canonical)
        restored.recompute()
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        self.codex_output.appendPlainText("\nRESTORE: document restored from protected in-memory snapshot (%s)." % reason)
        return restored
    def _backend_launch(self, backend_id, model_id, project, context, visual_paths=None):
        backend = self.backends[backend_id]
        if not backend["available"]:
            raise RuntimeError("Backend is not installed: %s" % backend["label"])
        mcp = self._mcp_settings(project)
        args = agentsmith_backends.build_launch_args(
            backend_id, model_id, project, context, visual_paths, mcp=mcp)
        if mcp and backend_id == "copilot":
            self._log("MCP is on, but copilot has no per-invocation MCP flag — "
                      "that run uses the bridge CLI client.")
        return backend["command"], args
    def _ensure_bridge_client(self, project):
        """Make sure the agent-facing tools exist in the task's working directory.

        The harness and prompts reference these by relative path; when a task runs
        from a project folder that never had them (discovered live: the reviewer burned
        budget hunting for a copy in a sibling directory), drop in the addon's copies.
        Ships: the bridge client, the slicer check tool and its config."""
        addon_dir = os.path.dirname(os.path.abspath(__file__))
        # Everything here is addon-owned and refreshed, the config included.
        #
        # It used to be exempt, on the reasoning that printer hosts and custom
        # profiles are the user's to edit. The effect was the opposite of the
        # intention: the copied tools look for the config NEXT TO THEMSELVES, so a
        # copy made weeks ago permanently shadowed the real file. Live consequence
        # — the agent was told the CORE One had no PLA profile and no network
        # address, both of which had been configured hours earlier, and it
        # correctly refused to print rather than guess. The canonical config is
        # the addon's; the project copy is a derived artefact and is kept current.
        # (copy2 preserves the source mtime, so an already-current copy is not
        # rewritten.) A user who wants per-project settings should edit the addon
        # config or pass --config, not rely on a stale copy nobody can see.
        for name in agentsmith_harness.AGENT_TOOLS:
            try:
                target = os.path.join(project, name)
                source = os.path.join(addon_dir, name)
                if not os.path.isfile(source):
                    continue
                target_mtime = (os.path.getmtime(target)
                                if os.path.isfile(target) else None)
                if not agentsmith_harness.should_refresh(
                        name, target_mtime, os.path.getmtime(source)):
                    continue
                shutil.copy2(source, target)
                self._log("Agent tool copied into project: %s" % target)
            except Exception as exc:
                self._log("Agent tool copy warning (%s): %s" % (name, exc))
        return os.path.join(project, "freecad_bridge_client.py")
    def _printer_context(self):
        """The fleet as the monitor last saw it, for the task context."""
        try:
            summary = printer_book.status_summary(printer_book.load_book())
        except Exception:
            return ""
        if not summary:
            return ""
        return ("PRINTERS (live state from the panel monitor; re-check with "
                "`python3 print_send.py --status` before relying on it):\n%s\n\n"
                % summary)
    def _collect_references(self, doc, task_id):
        """Parse the reference input into local image paths + URLs and record a manifest.

        Returns a dict {dir, local_images, urls, manifest, skipped}. Any IO failure degrades
        to an empty reference set so a bad reference can NEVER abort the task.
        """
        empty = {"dir": "", "local_images": [], "urls": [], "manifest": "", "skipped": []}
        try:
            raw = self.reference_input.toPlainText()
        except Exception:
            return dict(empty)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return dict(empty)
        image_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        source_images, urls, skipped = [], [], []
        for line in lines:
            lower = line.lower()
            if lower.startswith("http://") or lower.startswith("https://"):
                urls.append(line)
            elif os.path.isfile(line) and lower.endswith(image_exts):
                source_images.append(line)
            else:
                skipped.append(line)
        try:
            base = os.path.dirname(doc.FileName) if doc.FileName else self._project_directory()
            root = os.path.join(base, ".agentsmith", "reference", task_id)
            os.makedirs(root, exist_ok=True)
            copied = []
            used_names = set()
            for src in source_images:
                name = os.path.basename(src)
                target = os.path.join(root, name)
                if target in used_names or os.path.exists(target):
                    stem, ext = os.path.splitext(name)
                    index = 1
                    while True:
                        candidate = os.path.join(root, "%s-%d%s" % (stem, index, ext))
                        if candidate not in used_names and not os.path.exists(candidate):
                            target = candidate
                            break
                        index += 1
                shutil.copy2(src, target)
                used_names.add(target)
                copied.append(os.path.abspath(target))
            # Download URL references up front so they reach the backend as REAL vision
            # input at launch. A URL left as text is a reference in name only: codex-style
            # backends cannot add images mid-run, and (observed live) the worker simply
            # ignores "you may fetch" — so the panel fetches, and a failed download is
            # reported instead of silently dropped.
            downloaded_map = {}
            failed_urls = []
            for index, url in enumerate(urls):
                path = self._download_reference_image(url, root, index)
                if path:
                    copied.append(path)
                    downloaded_map[path] = url
                else:
                    failed_urls.append(url)
            manifest_path = os.path.join(root, "reference-manifest.json")
            manifest = {"task_id": task_id, "local_images": copied, "urls": urls,
                        "downloaded_from_urls": downloaded_map, "failed_urls": failed_urls,
                        "skipped": skipped, "note": "user-supplied references"}
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
            return {"dir": root, "local_images": copied, "urls": urls,
                    "failed_urls": failed_urls, "manifest": manifest_path, "skipped": skipped}
        except Exception as exc:
            self._log("Reference input warning: %s" % exc)
            return dict(empty)
    def _download_reference_image(self, url, root, index):
        """Fetch one reference URL into the task's reference dir. Returns the local
        path, or None when the URL is not a usable image (never raises)."""
        try:
            import urllib.request
            request = urllib.request.Request(url, headers={"User-Agent": "FreeCAD-AgentSmith/%s" % BRIDGE_VERSION})
            with urllib.request.urlopen(request, timeout=15) as response:
                content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
                data = response.read(15 * 1024 * 1024 + 1)
            if len(data) > 15 * 1024 * 1024:
                raise ValueError("image larger than 15 MiB")
            extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                         "image/bmp": ".bmp"}.get(content_type)
            if extension is None:
                url_ext = os.path.splitext(url.split("?", 1)[0])[1].lower()
                if url_ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                    extension = url_ext
                else:
                    raise ValueError("not an image (Content-Type %s)" % (content_type or "unknown"))
            target = os.path.join(root, "reference-url-%d%s" % (index + 1, extension))
            with open(target, "wb") as handle:
                handle.write(data)
            return os.path.abspath(target)
        except Exception as exc:
            self._log("Reference URL download failed (%s): %s" % (url, exc))
            return None
    def _create_visual_context(self, doc, task_id, phase, views=None):
        root = os.path.join(os.path.dirname(doc.FileName), ".agentsmith", "visual-context", task_id, phase)
        os.makedirs(root, exist_ok=True)
        views = views or ["current"]
        objects = []
        for obj in doc.Objects:
            item = {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId,
                    "visible": bool(getattr(obj, "Visibility", False))}
            shape = getattr(obj, "Shape", None)
            if shape is not None and not shape.isNull():
                box = shape.BoundBox
                item.update({"shape_type": shape.ShapeType, "solids": len(shape.Solids),
                             "volume": shape.Volume,
                             "bounding_box": {"min": [box.XMin, box.YMin, box.ZMin],
                                              "max": [box.XMax, box.YMax, box.ZMax]}})
            objects.append(item)
        try:
            digest = self.server._model_digest({"document": doc.Name})
        except Exception as exc:
            digest = {"error": str(exc)}
        manifest = {
            "document": doc.Name, "file": os.path.abspath(doc.FileName), "task_id": task_id,
            "phase": phase, "captured_views": views,
            "selection": [obj.Name for obj in Gui.Selection.getSelection()],
            "objects": objects, "model_digest": digest,
        }
        manifest_path = os.path.join(root, "scene-manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        view = Gui.activeDocument().activeView()
        camera_state = _camera_snapshot(view)
        orientation_methods = {
            "front": view.viewFront, "rear": view.viewRear, "top": view.viewTop,
            "bottom": view.viewBottom, "left": view.viewLeft, "right": view.viewRight,
        }
        images = []
        try:
            # Capture the current camera first, without disturbing it.
            if "current" in views:
                path = os.path.join(root, "current.png")
                view.saveImage(path, 1100, 850, "Current")
                images.append(path)
            for name in views:
                if name == "current":
                    continue
                orient = orientation_methods.get(name)
                if orient is None:
                    continue
                orient()
                view.fitAll()
                QtGui.QApplication.processEvents()
                path = os.path.join(root, name + ".png")
                view.saveImage(path, 1100, 850, "Current")
                images.append(path)
        finally:
            _camera_apply(view, camera_state)
            QtGui.QApplication.processEvents()
        return {"directory": root, "manifest": manifest_path, "images": images, "captured_views": views}
    def _run_codex(self):
        prompt = self.codex_prompt.toPlainText().strip()
        if not prompt or self.codex_process.state() != QtCore.QProcess.NotRunning:
            return
        if App.ActiveDocument is None:
            QtWidgets.QMessageBox.warning(self, "No document", "Open a FreeCAD document before starting an editing task.")
            return
        if not self.server.server.isListening():
            self.server.start(self.port.value())
        self.server.ensure_discovery()
        # A new edit task takes priority over any advisory reviewer still running —
        # stop it BEFORE switching the bridge back to edit mode so it can never mutate.
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self._stop_reviewer()
        self.access.setCurrentIndex(2)
        doc = App.ActiveDocument
        backend_id = self.backend.currentData()
        backend = self.backends[backend_id]
        model_id = str(self.model.currentData() or "")
        model_label = self.model.currentText()
        # A corrective round may escalate: the model that already failed on this part
        # gets replaced by a stronger one rather than being asked to try harder.
        escalated = getattr(self, "_autofix_model", None)
        if escalated:
            model_id = escalated
            model_label = "%s (escalated)" % escalated
        if not doc.FileName:
            QtWidgets.QMessageBox.warning(self, "Document is not saved", "Save the document as .FCStd first. The supervisor needs a fixed canonical path for protection and restore.")
            return
        doc.recompute()
        validation = _validate_document(doc)
        if not validation["ok"]:
            QtWidgets.QMessageBox.warning(self, "Document is not valid", "The task cannot be started safely because the starting document failed validation.")
            return
        doc.save()
        canonical_path = os.path.abspath(doc.FileName)
        if not _fcstd_archive_valid(canonical_path):
            QtWidgets.QMessageBox.critical(self, "Invalid FCStd", "The saved document is not a valid FCStd archive. The backend was not started.")
            return
        with open(canonical_path, "rb") as handle:
            protected_bytes = handle.read()
        task_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        checkpoint = self.server._checkpoint({"document": doc.Name, "label": "task-before-" + task_id})["path"]
        self.codex_task = {
            "id": task_id,
            "prompt": prompt,
            "document": doc.Name,
            "before_fingerprint": _document_fingerprint(doc),
            "before_undo_count": doc.UndoCount,
            "before_event_sequence": self.server.event_sequence,
            "checkpoint": checkpoint,
            "backend": backend_id,
            "model": model_id,
            "started": time.time(),
            "canonical_path": canonical_path,
            "file_backup_bytes": protected_bytes,
            "file_backup_sha256": hashlib.sha256(protected_bytes).hexdigest(),
            "missing_checks": 0,
            "assistant_messages": [],
            "raw_backend_output": [],
            "rss_before": _rss_bytes(),
            "budget_seconds": int(self.budget_choice.currentData() or LIVE_EDIT_BUDGET_SECONDS),
        }
        if getattr(self, "_autofix_pending", False):
            self._autofix_pending = False
            self.codex_task["autofix_round"] = True
        else:
            # A new user-initiated task starts a fresh corrective chain; otherwise the
            # previous part's findings would count against this one's round budget.
            self._autofix_chain = None
        self.server.protected_documents[doc.Name] = canonical_path
        self.server.supervised_metrics = {"task_id": task_id, "mutation_events": 0, "object_events": {}}
        self._append_document_history(canonical_path, {
            "status": "pending", "task_id": task_id, "prompt": prompt,
            "backend": backend_id, "model": model_id, "started": self.codex_task["started"],
            "checkpoint_before": checkpoint, "canonical_path": canonical_path,
        })
        capture_views = self._selected_capture_views()
        self.codex_task["capture_views"] = capture_views
        self.codex_task["camera_before"] = _camera_snapshot(Gui.activeDocument().activeView())
        # Whether any visible solid existed before the task. If the task CREATES the
        # first geometry, restoring the old camera would show the user an empty view
        # (observed live) — in that case we frame the new model instead.
        had_visible_geometry = False
        try:
            for existing_obj in doc.Objects:
                existing_shape = getattr(existing_obj, "Shape", None)
                if (existing_shape is not None and not existing_shape.isNull()
                        and getattr(existing_shape, "Solids", None)
                        and getattr(existing_obj, "Visibility", False)):
                    had_visible_geometry = True
                    break
        except Exception:
            had_visible_geometry = True
        self.codex_task["had_visible_geometry_before"] = had_visible_geometry
        try:
            visual_context = self._create_visual_context(doc, task_id, "before", capture_views)
        except Exception as exc:
            visual_context = {"directory": "", "manifest": "", "images": []}
            self._log("Visual context warning: %s" % exc)
        self.codex_task["visual_before"] = visual_context
        try:
            reference = self._collect_references(doc, task_id)
        except Exception as exc:
            reference = {"dir": "", "local_images": [], "urls": [], "manifest": "", "skipped": []}
            self._log("Reference input warning: %s" % exc)
        self.codex_task["reference"] = reference
        reference_local_images = list(reference.get("local_images", []))
        reference_urls = list(reference.get("urls", []))
        doc.openTransaction("FreeCAD Chat task " + task_id)
        self.codex_task["transaction_open"] = True
        project = self._project_directory()
        self._ensure_bridge_client(project)
        document = doc.FileName
        kind_label, harness_text = self._harness_text()
        self.codex_task["task_kind"] = "auto"
        budget_minutes = max(1, self.codex_task["budget_seconds"] // 60)
        delivery_contract = (
            "TIME BUDGET & DELIVERY CONTRACT (read first):\n"
            "- You have a HARD wall-clock limit of about %d minutes from the moment this task started. "
            "When it is exceeded a watchdog terminates you (SIGTERM). A live document that already "
            "verifies (valid geometry, mutations through the bridge) is KEPT and saved, but an unverified "
            "change is rolled back, and anything you have not written to disk yet — the report, the "
            "checklist, screenshots — is lost. An unfinished investigation with no mutation counts as a "
            "FAILED task with zero value.\n"
            "- Order of work once the geometry is verified: save the document, write the verification "
            "report to disk, THEN do optional research (web look-ups for fasteners, anchors, load tables). "
            "Research is strictly optional once the model is verified; never let it eat the budget.\n"
            "- Your job is to DELIVER a verified change, not to investigate exhaustively. Diagnose quickly, "
            "then act. The moment you have a plausible root cause, implement the fix through the bridge "
            "BEFORE doing any further analysis. Analysis that never produces a live mutation is a failure.\n"
            "- Budget your time: spend at most the first third on diagnosis, apply the fix in the middle third, "
            "and reserve the final third for validate/save/screenshot. If unsure how long you have been running, "
            "check the wall clock with `date` and compare against the start time.\n"
            "- Prefer a concrete, reversible parametric change early and iterate over it, rather than seeking "
            "perfect certainty first. Fix the general/driving cause (e.g. a wrong Spreadsheet parameter or "
            "sketch expression), not a hard-coded value for this one model.\n"
            "- If diagnosis shows the correct fix is to change a driving parameter, expression or constraint, "
            "MAKE that change through the bridge now; do not merely report what should be changed.\n"
            "- Only report FAILED without a mutation if the live document is genuinely unreachable or the request "
            "is impossible; in that case say so early instead of burning the whole budget.\n"
        ) % budget_minutes
        reference_section = ""
        if reference_local_images or reference_urls:
            reference_lines = ["USER-SUPPLIED REFERENCES (MANDATORY visual target):"]
            if reference_local_images:
                reference_lines.append(
                    "- %d reference image(s) are ATTACHED as vision input (URL references were already "
                    "downloaded for you). They are the PRIMARY statement of what the user wants the part "
                    "to LOOK like — matching them is part of the task, not an optional inspiration:\n"
                    "  1. STUDY them before planning: describe to yourself the topology, proportions and "
                    "functional features they show.\n"
                    "  2. Your feature plan MUST name which reference features you are reproducing "
                    "(counts, arrangement, curves, interfaces).\n"
                    "  3. Your final verification MUST put your axonometric render beside the references "
                    "and enumerate matches vs deviations. An unexplained divergence in topology or "
                    "proportion is a DEFECT to fix, not a footnote.\n"
                    "  They are NOT dimensional truth: derive every dimension/tolerance from FreeCAD "
                    "geometry and the Parameters spreadsheet, never by scaling pixels."
                    % len(reference_local_images)
                )
                for path in reference_local_images:
                    reference_lines.append("    · %s" % path)
            failed_urls = list(reference.get("failed_urls", []))
            if failed_urls:
                reference_lines.append(
                    "- These reference URL(s) could NOT be downloaded as images; fetch or research them "
                    "yourself and note in the report what you used:")
                for url in failed_urls:
                    reference_lines.append("    · %s" % url)
            elif reference_urls and not reference_local_images:
                reference_lines.append("- Reference URL(s):")
                for url in reference_urls:
                    reference_lines.append("    · %s" % url)
            reference_lines.append("- Reference manifest: %s" % (reference.get("manifest") or "(none)"))
            reference_lines.append("- Reference dir (the reference.md playbook may extend it): %s"
                                   % (reference.get("dir") or "(none)"))
            reference_section = "\n".join(reference_lines) + "\n\n"
        context = (
            "You are an editing worker (%s, selected model: %s) supervised by the FreeCAD Chat panel.\n"
            "Task id: %s\nProject: %s\nLive active document: %s\n"
            "%s\n"
            "MODELING HARNESS (%s) — always-on methodology; apply each playbook whose trigger matches the part:\n%s\n\n"
            "PERSISTED HISTORY FOR THIS DOCUMENT (oldest to newest):\n%s\n\n"
            "VISUAL/GEOMETRIC CONTEXT BEFORE EDIT:\nManifest: %s\nViews: %s\n"
            "The manifest embeds a `model_digest` (dimensional ground truth). Refresh it any time with "
            "`python3 freecad_bridge_client.py model_digest '{}'`.\n\n"
            "%s"
            "%s"
            "Bridge client: %s/freecad_bridge_client.py\nDiscovery: %s\nCheckpoint already created: %s\n\n"
            "MANDATORY WORKFLOW:\n"
            "1. Run `python3 freecad_bridge_client.py ping`, `capabilities`, `document_info`, and `model_digest` before acting.\n"
            "2. Diagnose only as much as needed to choose a fix, then modify the ALREADY OPEN document through "
            "freecad_bridge_client.py. Prefer `batch` for structured operations; use `execute_python` only when necessary. "
            "Do not merely edit generator scripts and do not open the FCStd in another FreeCAD process.\n"
            "3. Every actual model mutation must go through the bridge so it receives FreeCAD Undo transactions. Preserve native parametrism and follow AGENTS.md.\n"
            "   NEVER close or reopen the document, use saveAs, change its path, rename/move/delete the FCStd, or launch another FreeCAD process. The canonical file is protected by a live watchdog.\n"
            "4. Inspect the supplied images and scene manifest before choosing a method. Images are visual evidence, not dimensional proof. Derive task-specific measurements from FreeCAD geometry and numerically verify every requested dimension/tolerance.\n"
            "5. After editing, capture comparable views, inspect them for visible regressions, run bridge `validate`, then `save`, `fit_view`, and a final `screenshot`.\n"
            "6. Report exact changed FreeCAD objects, parameters, numerical checks, and visual comparison. If you cannot change or inspect the live document, report FAILED; never claim success based only on source-file edits.\n"
            "7. Manufacturing requests (slice / print / send-to-printer), alone or combined with modeling, are legitimate tasks — follow the 'Manufacturing' playbook and use slice_check.py / print_send.py from the project directory. If the task intentionally made NO model mutation (slice-only, print-only, status query), end your final message with the exact line 'ACTION-ONLY TASK COMPLETED: <summary>' so the supervisor accepts it; never use that line after a failure.\n\n"
            "%s"
            "USER REQUEST: %s"
            % (backend["label"], model_id or "backend default", task_id, project, document,
               delivery_contract,
               kind_label, harness_text or "(no harness playbook loaded)",
               self._history_context(document), visual_context["manifest"], json.dumps(visual_context["images"]),
               reference_section,
               # MCP note lives in the task context, not in the harness .md files:
               # the harness is shared with the eval runner, which drives the bridge
               # through the CLI client. Forking it would create exactly the kind of
               # silent prompt drift that already bit this project once.
               # Live printer states, kept current by the panel's monitor. A print
               # request should not begin by rediscovering the fleet, and a machine
               # that is switched off should be known to be switched off before the
               # task plans around it. Readings that have gone stale are reported as
               # unknown rather than quoted.
               self._printer_context(),
               ("BRIDGE TOOLS: this session also exposes the bridge as MCP tools "
                "(server '%s') with typed schemas. PREFER them over shelling out to "
                "freecad_bridge_client.py — same commands, same arguments, no shell "
                "quoting and no output parsing. The CLI client below stays available "
                "as a fallback.\n\n" % agentsmith_backends.MCP_SERVER_NAME)
               if self.mcp_enabled.isChecked() else "",
               project, DISCOVERY_FILE, checkpoint, prompt)
        )
        attached = list(visual_context["images"]) + reference_local_images
        program, arguments = self._backend_launch(backend_id, model_id, project, context, attached)
        self.codex_output.appendPlainText("\n› [%s / %s] %s\n" % (backend["label"], model_label, prompt))
        self.codex_output.appendPlainText("Supervisor checkpoint: " + checkpoint)
        self.codex_supervisor_status.setText("Supervisor: running task %s" % task_id)
        self.task_stop_requested = False
        self.task_started_monotonic = time.monotonic()
        self.task_elapsed.setText("0:00")
        self._set_task_state("running", "Working · %s · %s" % (backend["label"], model_label))
        self.task_timer.start()
        self.file_guard_timer.start()
        self.codex_prompt.clear()
        self.codex_run.setEnabled(False)
        self.codex_stop.setEnabled(True)
        self.backend.setEnabled(False)
        self.model.setEnabled(False)
        self.codex_full_access.setEnabled(False)
        self.codex_buffer = ""
        self.chat_seen = set()
        self.codex_process.setWorkingDirectory(project)
        process_environment = QtCore.QProcessEnvironment.systemEnvironment()
        for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
            process_environment.remove(variable)
        self.codex_process.setProcessEnvironment(process_environment)
        self.codex_process.start(program, arguments)
        self.codex_process.closeWriteChannel()
    def _stop_codex(self):
        if self.codex_process.state() != QtCore.QProcess.NotRunning:
            self.task_stop_requested = True
            self.task_status.setText("Stopping…")
            self.codex_process.terminate()
            QtCore.QTimer.singleShot(2500, self._kill_codex_if_running)
        elif self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.codex_output.appendPlainText("\nReviewer: stopping on request.")
            self._stop_reviewer()
            self.codex_stop.setEnabled(False)
            self.codex_supervisor_status.setText("Reviewer: stopped")
    def _kill_codex_if_running(self):
        if self.codex_process.state() != QtCore.QProcess.NotRunning:
            self.codex_process.kill()
    def _codex_output_ready(self):
        self.codex_buffer += bytes(self.codex_process.readAllStandardOutput()).decode("utf-8", "replace")
        lines = self.codex_buffer.split("\n")
        self.codex_buffer = lines.pop()
        for line in lines:
            if self.codex_task and line.strip():
                self.codex_task["raw_backend_output"].append(line[-4000:])
                self.codex_task["raw_backend_output"] = self.codex_task["raw_backend_output"][-80:]
            try:
                event = json.loads(line)
                for text in self._backend_event_text(event):
                    key = (self.codex_task.get("backend") if self.codex_task else "", text)
                    if text and key not in self.chat_seen:
                        self.chat_seen.add(key)
                        self.codex_output.appendPlainText(text)
                        if self.codex_task:
                            self.codex_task["assistant_messages"].append(text)
            except Exception:
                self.codex_output.appendPlainText(line)
    def _backend_event_text(self, event, backend=None):
        if backend is None:
            backend = self.codex_task.get("backend") if self.codex_task else ""
        return agentsmith_backends.backend_event_text(event, backend)
    def _codex_finished(self, exit_code, exit_status):
        self._codex_output_ready()
        camera_before = self.codex_task.get("camera_before") if self.codex_task else None
        had_geometry_before = self.codex_task.get("had_visible_geometry_before", True) if self.codex_task else True
        if self.codex_buffer.strip():
            trailing_output = self.codex_buffer.strip()
            self.codex_output.appendPlainText(trailing_output)
            if self.codex_task:
                self.codex_task["raw_backend_output"].append(trailing_output[-4000:])
        self.codex_buffer = ""
        backend_label = self.backends.get(self.codex_task.get("backend") if self.codex_task else "", {}).get("label", "Backend")
        self.codex_output.appendPlainText("\n%s finished (exit %d)." % (backend_label, exit_code))
        try:
            outcome = self._supervise_codex_result(exit_code)
        except Exception as exc:
            task = self.codex_task
            self.file_guard_timer.stop()
            if task:
                try:
                    self._restore_task_snapshot(task, "supervisor exception: %s" % exc)
                finally:
                    self.server.protected_documents.pop(task["document"], None)
                    self.server.supervised_metrics = None
                    self.codex_task = None
            outcome = {"status": "failed", "reason": "supervisor exception: %s" % exc, "restored": bool(task)}
            self.codex_output.appendPlainText("\nSUPERVISOR ERROR: " + str(exc))
        if self.task_stop_requested:
            self._set_task_state("stopped", "Stopped")
            self.codex_output.appendPlainText("\n■ TASK STOPPED")
        elif outcome and outcome.get("status") == "success":
            self._set_task_state("success", "Done · verified")
            self.codex_output.appendPlainText("\n✓ TASK DONE AND VERIFIED")
        else:
            self._set_task_state("failed", "Failed · no verified change")
            self.codex_output.appendPlainText("\n✗ TASK FAILED OR WAS NOT VERIFIED")
        self._update_task_elapsed()
        try:
            if Gui.activeDocument() is not None:
                view = Gui.activeDocument().activeView()
                task_succeeded = bool(outcome and outcome.get("status") == "success")
                if task_succeeded and not had_geometry_before:
                    # The task created the document's first geometry: make sure the new
                    # solids are visible and framed, instead of restoring a camera that
                    # was looking at an empty scene.
                    try:
                        active = App.ActiveDocument
                        for created_obj in (active.Objects if active else []):
                            created_shape = getattr(created_obj, "Shape", None)
                            if (created_shape is not None and not created_shape.isNull()
                                    and getattr(created_shape, "Solids", None)
                                    and not getattr(created_obj, "Visibility", False)
                                    # only top-level results: inputs consumed by a boolean/
                                    # feature (non-empty InList) stay hidden by design
                                    and not getattr(created_obj, "InList", None)):
                                created_obj.Visibility = True
                    except Exception as exc:
                        self._log("Visibility fixup warning: %s" % exc)
                    view.viewAxonometric()
                    view.fitAll()
                elif camera_before is not None:
                    _camera_apply(view, camera_before)
                QtGui.QApplication.processEvents()
        except Exception as exc:
            self._log("Camera restore warning: %s" % exc)
        self.codex_run.setEnabled(True)
        self.codex_stop.setEnabled(False)
        self.backend.setEnabled(True)
        self.model.setEnabled(True)
        self.codex_full_access.setEnabled(True)
        try:
            self._maybe_start_reviewer(outcome)
        except Exception as exc:
            self._log("Reviewer start warning: %s" % exc)
    def _run_prechecks(self):
        """Measure everything a machine can measure, before the reviewer is launched.

        The reviewer used to spend its budget fetching these numbers one tool call
        at a time — and, being a language model, could get them wrong. Handing them
        over as facts leaves it the part it is actually good at: does this match the
        request and the reference. Every command is independent, so one that fails
        is reported as failed instead of sinking the rest.
        """
        results = {}
        for command in agentsmith_review.PRECHECK_COMMANDS:
            handler = getattr(self.server, "_" + command, None)
            if command == "print_readiness":
                handler = self.server._print_readiness
            if command == "model_digest":
                handler = self.server._model_digest
            if handler is None:
                continue
            try:
                results[command] = handler({})
            except Exception as exc:
                results[command] = str(exc)
        return results
    def _maybe_start_reviewer(self, outcome):
        """After a VERIFIED-successful task, optionally run an independent read-only
        reviewer agent (judge != maker). It only inspects and appends an advisory
        verdict; it never mutates the document (the bridge is forced read-only)."""
        if self.task_stop_requested:
            return
        if not outcome or outcome.get("status") != "success":
            return
        if outcome.get("action_only"):
            return  # nothing was modeled; there is no geometry change to review
        if not self.reviewer_enabled.isChecked():
            return
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            return
        if self.codex_process.state() != QtCore.QProcess.NotRunning or self.codex_task:
            return
        if App.ActiveDocument is None:
            return
        backend_id = outcome.get("backend") or self.backend.currentData()
        backend = self.backends.get(backend_id)
        if not backend or not backend.get("available"):
            self.codex_output.appendPlainText("\nReviewer skipped: no backend available.")
            return
        model_id = str(outcome.get("model") or "")
        project = self._project_directory()
        self._ensure_bridge_client(project)

        def _existing(paths, limit):
            found = []
            for candidate in (paths or []):
                try:
                    if candidate and os.path.isfile(candidate):
                        found.append(candidate)
                except Exception:
                    pass
                if len(found) >= limit:
                    break
            return found

        after_imgs = _existing((outcome.get("visual_after") or {}).get("images"), 6)
        before_imgs = _existing((outcome.get("visual_before") or {}).get("images"), 2)
        reference = outcome.get("reference") or {}
        ref_imgs = _existing(reference.get("local_images"), 4)
        images = after_imgs + ref_imgs + before_imgs

        ref_note = ""
        if ref_imgs or reference.get("urls"):
            ref_note = ("\nREFERENCE TARGET: %d reference image(s) attached and/or URLs %s. The user "
                        "supplied these as the intended look of the part. Compare the result renders "
                        "against them feature by feature (topology, proportions, functional elements). "
                        "If the model clearly diverges from the references and the worker's summary "
                        "gives no justification, that ALONE is VERDICT: CONCERNS — name the diverging "
                        "features concretely so the fix round can target them.\n"
                        % (len(ref_imgs), json.dumps(reference.get("urls") or [], ensure_ascii=False)))

        prompt = outcome.get("prompt", "")
        changed = ", ".join(outcome.get("changed_objects") or []) or "(none recorded)"
        worker_summary = (outcome.get("response") or "")[-1800:]

        review_context = (
            "You are an INDEPENDENT QA REVIEWER for a FreeCAD model. A worker agent just "
            "reported a task as done and the supervisor verified a valid live change. Judge "
            "whether that change actually satisfies the request and any reference — you are the "
            "judge, not the maker.\n\n"
            "HARD RULE: the bridge is in READ-ONLY mode for you. Do NOT modify, create, remove, "
            "recompute or save anything; mutating commands WILL be rejected. Only inspect.\n\n"
            "Bridge client: %s/freecad_bridge_client.py   Discovery: %s\n"
            "Use ONLY read commands: ping, capabilities, document_info, object_info, model_digest, "
            "measure, cross_section, check_solid, interference_check, mass_properties, validate, "
            "sketch_info, spreadsheet_info.\n\n"
            "ORIGINAL USER REQUEST:\n%s\n\n"
            "WHAT THE WORKER CHANGED: %s\n"
            "WORKER'S OWN SUMMARY (may be optimistic — verify it, do not trust it):\n%s\n"
            "%s\n"
            "ATTACHED IMAGES: result renders first, then any reference images, then a 'before' "
            "frame. Images are shape evidence only; confirm every number via model_digest / measure "
            "/ cross_section.\n\n"
            "ALREADY MEASURED FOR YOU (deterministic, straight from the geometry — treat "
            "these as facts and do NOT spend your budget re-fetching them):\n%s\n\n"
            "Work quickly and decisively (a few minutes):\n"
            "1. Read the measurements above first. Re-measure ONLY what they do not cover "
            "(use `measure` for specific gaps, `cross_section` for internal walls).\n"
            "2. Judge the numbers against the REQUEST: which requested dimension, tolerance "
            "or feature is not met? That comparison is your job; the measuring is done.\n"
            "3. Compare the result render against the request and reference; list only REAL deviations.\n"
            "5. FUNCTIONAL SANITY (dimensions passing is NOT enough): mentally put the part into its "
            "real use. Identify the mounting/contact surface, the fastener axes, the load direction "
            "and the working feature (opening, hook, slot, cavity). Check they are mutually "
            "consistent — e.g. screw holes must be perpendicular to the mounting plane, a hook's "
            "opening must face away from the wall and oppose gravity, a lid must clear its box. A "
            "part whose features all measure correctly but which cannot physically do its job is a "
            "VERDICT: CONCERNS with a one-line explanation of why it fails in use.\n"
            "   PROJECTION CHECK: matching a reference in ONE view proves nothing. Compare the "
            "model's bounding box extents against the functional pose: the working feature must "
            "actually PROTRUDE along the away-from-mounting-surface axis (a wall part whose depth "
            "in that axis is only its material thickness is a flat lookalike — the profile was "
            "built in the wrong projection plane; classic failure, always check).\n\n"
            "OUTPUT (concise, no preamble): first line EXACTLY 'VERDICT: PASS' or 'VERDICT: CONCERNS', "
            "then up to ~6 bullets of concrete, checked findings (each with the measured number), then a "
            "'Suggested fixes:' line naming the specific Spreadsheet alias / sketch datum / expression to "
            "change and to what. If everything matches, say so briefly."
            % (project, DISCOVERY_FILE, prompt, changed, worker_summary, ref_note,
               agentsmith_review.format_precheck_report(self._run_prechecks()))
        )

        program, arguments = self._backend_launch(backend_id, model_id, project, review_context, images)

        self.reviewer_run_id += 1
        run_id = self.reviewer_run_id
        prev_access = self.access.currentIndex()
        self.access.setCurrentIndex(0)  # hard read-only guarantee for the reviewer
        self.reviewer_state = {"backend": backend_id, "model": model_id,
                               "prev_access_index": prev_access, "run_id": run_id,
                               "started": time.time(), "text": [],
                               "prompt": outcome.get("prompt", ""),
                               "autofix_round": bool(outcome.get("autofix_round"))}
        self.reviewer_buffer = ""
        self.codex_output.appendPlainText("\n── VERIFICATION PASS (reviewer) — independent, read-only ──")
        self.codex_supervisor_status.setText("Reviewer: running (read-only)")
        self.codex_stop.setEnabled(True)
        self.reviewer_process.setWorkingDirectory(project)
        process_environment = QtCore.QProcessEnvironment.systemEnvironment()
        for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
            process_environment.remove(variable)
        self.reviewer_process.setProcessEnvironment(process_environment)
        self.reviewer_process.start(program, arguments)
        self.reviewer_process.closeWriteChannel()
        QtCore.QTimer.singleShot(480000, lambda rid=run_id: self._reviewer_timeout(rid))
    def _reviewer_output_ready(self):
        self.reviewer_buffer += bytes(self.reviewer_process.readAllStandardOutput()).decode("utf-8", "replace")
        lines = self.reviewer_buffer.split("\n")
        self.reviewer_buffer = lines.pop()
        backend = (self.reviewer_state or {}).get("backend", "")
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                for text in self._backend_event_text(event, backend):
                    if text:
                        if self.reviewer_state is not None:
                            self.reviewer_state["text"].append(text)
                            self.reviewer_state["text"] = self.reviewer_state["text"][-60:]
                        self.codex_output.appendPlainText(text)
            except Exception:
                if self.reviewer_state is not None:
                    self.reviewer_state["text"].append(line)
                    self.reviewer_state["text"] = self.reviewer_state["text"][-60:]
                self.codex_output.appendPlainText(line)
    def _reviewer_finished(self, exit_code, exit_status):
        self._reviewer_output_ready()
        if self.reviewer_buffer.strip():
            trailing = self.reviewer_buffer.strip()
            self.codex_output.appendPlainText(trailing)
            if self.reviewer_state is not None:
                self.reviewer_state["text"].append(trailing)
        self.reviewer_buffer = ""
        state = self.reviewer_state
        self.reviewer_state = None
        try:
            self.access.setCurrentIndex(state.get("prev_access_index", 2) if state else 2)
        except Exception:
            pass
        self.codex_stop.setEnabled(False)
        self.codex_output.appendPlainText("\n── End of the verification pass ──")
        self.codex_supervisor_status.setText("Reviewer: done")
        verdict_text = "\n".join((state or {}).get("text", []))[-6000:]
        try:
            doc = App.ActiveDocument
            if doc and doc.FileName and verdict_text.strip():
                self._append_document_history(os.path.abspath(doc.FileName), {
                    "status": "review", "finished": time.time(),
                    "backend": (state or {}).get("backend"), "model": (state or {}).get("model"),
                    "verdict": verdict_text, "exit_code": exit_code,
                })
                self._refresh_document_history(True)
        except Exception as exc:
            self._log("Reviewer history warning: %s" % exc)
        try:
            self._maybe_autofix(state, verdict_text)
        except Exception as exc:
            self._log("Autofix warning: %s" % exc)
        if agentsmith_review.VERDICT_CONCERNS in (verdict_text or ""):
            self._offer_lesson((state or {}).get("prompt", ""),
                               "reviewer raised concerns after a verified change",
                               verdict_text)
    def _offer_lesson(self, prompt, outcome_summary, verdict_text=""):
        """Arm the "Zapsat lekci" button with what just went wrong.

        Only armed, never fired: the entry is drafted when the user asks for it.
        `lessons.md` is read by the modeling agent as binding, so nothing lands in
        it without a person having seen the words first.
        """
        self._lesson_source = {"prompt": prompt or "", "outcome": outcome_summary or "",
                               "verdict": verdict_text or ""}
        self.lesson_button.setEnabled(True)
        self.codex_output.appendPlainText(
            "\nTip: the \u201cRecord lesson\u201d button drafts a lessons.md entry from this.")
    def _lessons_path(self):
        return os.path.join(HARNESS_DIR, "lessons.md")
    def _draft_lesson(self):
        """Ask a short backend run for one lessons.md entry, then let the user decide."""
        source = self._lesson_source
        if not source:
            return
        if self.codex_process.state() != QtCore.QProcess.NotRunning or self.codex_task:
            QtWidgets.QMessageBox.information(
                self, "A task is running", "Wait until the current task finishes.")
            return
        backend_id = self.backend.currentData()
        backend = self.backends.get(backend_id)
        if not backend or not backend.get("available"):
            QtWidgets.QMessageBox.warning(self, "No backend available",
                                          "Drafting a lesson needs an available backend.")
            return
        try:
            with open(self._lessons_path(), "r", encoding="utf-8") as handle:
                existing = handle.read()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "lessons.md", "Cannot be read: %s" % exc)
            return

        prompt = agentsmith_lessons.build_prompt(
            agentsmith_lessons.next_number(existing), source["prompt"], source["outcome"],
            source["verdict"], time.strftime("%Y-%m-%d"))
        project = self._project_directory()
        program, arguments = self._backend_launch(
            backend_id, str(self.model.currentData() or ""), project, prompt)

        self.lesson_button.setEnabled(False)
        self.codex_output.appendPlainText("\n── Lesson draft (short run) ──")
        self._lesson_process = QtCore.QProcess(self)
        self._lesson_process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self._lesson_process.setWorkingDirectory(project)
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
            environment.remove(variable)
        self._lesson_process.setProcessEnvironment(environment)
        self._lesson_buffer = []
        self._lesson_process.readyReadStandardOutput.connect(self._lesson_output_ready)
        self._lesson_process.finished.connect(
            lambda code, status, existing=existing: self._lesson_finished(code, existing))
        self._lesson_process.start(program, arguments)
        self._lesson_process.closeWriteChannel()
        QtCore.QTimer.singleShot(180000, self._kill_lesson_if_running)
    def _lesson_output_ready(self):
        chunk = bytes(self._lesson_process.readAllStandardOutput()).decode("utf-8", "replace")
        for line in chunk.split("\n"):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                self._lesson_buffer.append(line)
                continue
            self._lesson_buffer.extend(self._backend_event_text(
                event, self.backend.currentData()))
    def _kill_lesson_if_running(self):
        process = getattr(self, "_lesson_process", None)
        if process is not None and process.state() != QtCore.QProcess.NotRunning:
            process.kill()
    def _lesson_finished(self, exit_code, existing):
        text = "\n".join(self._lesson_buffer)
        self._lesson_buffer = []
        self.lesson_button.setEnabled(bool(self._lesson_source))
        entry, declined = agentsmith_lessons.extract_entry(text)
        if declined:
            self.codex_output.appendPlainText(
                "Lesson not recorded — the agent found no generalisable rule in it:\n%s"
                % entry[:600])
            return
        if not entry.strip():
            self.codex_output.appendPlainText(
                "The lesson draft could not be obtained (exit %s)." % exit_code)
            return

        problems = agentsmith_lessons.validate_entry(entry, existing)
        message = entry if not problems else (
            entry + "\n\n⚠ Formal objections:\n" + "\n".join("• " + p for p in problems))
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Record the lesson in lessons.md?")
        dialog.setText("Draft entry — write it into harness/lessons.md?")
        dialog.setInformativeText(message)
        dialog.setStandardButtons(QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel)
        dialog.setDefaultButton(QtWidgets.QMessageBox.Cancel)
        if dialog.exec_() != QtWidgets.QMessageBox.Save:
            self.codex_output.appendPlainText("Lesson not recorded (cancelled).")
            return

        try:
            path = self._lessons_path()
            with open(path, "r", encoding="utf-8") as handle:
                current = handle.read()
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(agentsmith_lessons.append_entry(current, entry))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "lessons.md", "Write failed: %s" % exc)
            return
        # The registry cache holds the harness text; drop it so the next task sees
        # the new rule instead of the version from panel start-up.
        self._harness_registry_cache = None
        self._lesson_source = None
        self.lesson_button.setEnabled(False)
        self.codex_output.appendPlainText("Lesson recorded in lessons.md.")
    def _maybe_autofix(self, state, verdict_text):
        """Corrective worker rounds while the reviewer's findings keep changing.

        This used to be exactly one round — a safe number, not a right one. One is
        too few when a fix uncovers the next problem, and any fixed N is too many
        when the model cannot fix the thing at all: it spends a whole task budget
        re-reporting the same complaint. So the loop stops on CONVERGENCE (the
        reviewer repeating itself) and the round limit is only a backstop.

        Every corrective run goes through the full normal pipeline: checkpoint,
        watchdog, supervisor verification and its own reviewer pass afterwards.
        """
        if not state:
            return
        chain = getattr(self, "_autofix_chain", None) or {"rounds": 0, "findings": set()}
        decision = agentsmith_review.autofix_decision(
            verdict_text,
            previous_findings=chain["findings"],
            rounds_done=chain["rounds"],
            max_rounds=int(self.autofix_rounds.currentData() or 1),
            enabled=self.autofix_enabled.isChecked(),
            busy=bool(self.codex_task) or self.codex_process.state() != QtCore.QProcess.NotRunning,
        )
        if not decision["run"]:
            if chain["rounds"] or decision["converged"]:
                # Say why the loop stopped; a silent stop reads as a bug.
                self.codex_output.appendPlainText(
                    "\nAUTO-OPRAVA: konec — %s." % decision["reason"])
            self._autofix_chain = None
            return

        doc = App.ActiveDocument
        if doc is None or not doc.FileName:
            self._autofix_chain = None
            return

        self._autofix_chain = {"rounds": chain["rounds"] + 1, "findings": decision["findings"]}
        original_prompt = state.get("prompt", "")
        findings = (verdict_text or "").strip()[-2500:]
        corrective = (
            "CORRECTIVE ROUND %d of at most %d (automatic): an independent read-only "
            "reviewer checked the previous change and raised concerns. Fix ONLY the "
            "concrete deviations below — do not redesign the part, do not touch anything "
            "the reviewer did not flag, prefer the reviewer's 'Suggested fixes' "
            "parameters. If a finding is wrong, say so with the measurement that "
            "disproves it instead of changing geometry.\n\n"
            "ORIGINAL REQUEST (already largely implemented):\n%s\n\n"
            "REVIEWER FINDINGS TO RESOLVE:\n%s"
            % (self._autofix_chain["rounds"], int(self.autofix_rounds.currentData() or 1),
               original_prompt, findings)
        )
        self._autofix_pending = True
        # Escalation: a model that already failed once on this part gets one more
        # chance with more capability behind it, if the user picked an escalation model.
        self._autofix_model = str(self.escalate_model.currentData() or "") or None
        escalation = " · eskalace na %s" % self.escalate_model.currentText() if self._autofix_model else ""
        self.codex_output.appendPlainText(
            "\nAUTO-OPRAVA: %s%s." % (decision["reason"], escalation))
        self.codex_prompt.setPlainText(corrective)
        try:
            self._run_codex()
        finally:
            # If _run_codex bailed before creating the task, the flags must not leak
            # into the next user-initiated run.
            self._autofix_pending = False
            self._autofix_model = None
    def _reviewer_timeout(self, run_id):
        state = self.reviewer_state
        if state and state.get("run_id") == run_id and self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.codex_output.appendPlainText("\nReviewer: time limit reached, terminating.")
            self.reviewer_process.terminate()
            QtCore.QTimer.singleShot(2000, self._kill_reviewer_if_running)
    def _kill_reviewer_if_running(self):
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.reviewer_process.kill()
    def _stop_reviewer(self):
        """Terminate a running reviewer without affecting the (already-finished) task."""
        self.reviewer_state = None
        self.reviewer_buffer = ""
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.reviewer_process.terminate()
            self.reviewer_process.waitForFinished(1500)
            if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
                self.reviewer_process.kill()
    def _codex_process_error(self, error):
        if self.codex_process.state() == QtCore.QProcess.NotRunning and self.codex_task:
            self._set_task_state("failed", "The backend could not be started")
            self.codex_output.appendPlainText("\nERROR: backend process could not start (%s)" % error)
            self.codex_run.setEnabled(True)
            self.codex_stop.setEnabled(False)
            self.backend.setEnabled(True)
            self.model.setEnabled(True)
            self.codex_full_access.setEnabled(True)
            self._supervise_codex_result(-1)
    def _supervise_codex_result(self, exit_code):
        task = self.codex_task
        self.file_guard_timer.stop()
        if not task:
            self.codex_supervisor_status.setText("Supervisor: no task metadata")
            return None
        canonical = task["canonical_path"]
        doc = _optional_document(task["document"])
        document_missing = doc is None
        path_changed = not document_missing and os.path.abspath(doc.FileName or "") != canonical
        # The archive check touches the disk, so it only runs once the cheaper
        # document checks have passed — same short-circuit as before the split.
        archive_invalid = (not document_missing and not path_changed
                           and not _fcstd_archive_valid(canonical))
        restore_reason = agentsmith_supervision.restore_reason(
            guard_violation=task.get("guard_violation"),
            document_missing=document_missing,
            path_changed=path_changed,
            archive_invalid=archive_invalid,
        )

        if restore_reason:
            doc = self._restore_task_snapshot(task, restore_reason)
            outcome = {"status": "failed", "reason": restore_reason, "exit_code": exit_code,
                       "restored": True, "changed": False, "bridge_events": self.server.event_sequence - task["before_event_sequence"],
                       "validation_ok": _validate_document(doc)["ok"]}
        else:
            doc.recompute()
            validation = _validate_document(doc)
            after_fingerprint = _document_fingerprint(doc)
            changed = after_fingerprint != task["before_fingerprint"]
            bridge_events = self.server.event_sequence - task["before_event_sequence"]
            # The document observer's mutation counter, not the fingerprint, is the
            # trustworthy "did anything really change" signal — see
            # agentsmith_supervision.classify_outcome for why.
            metrics = self.server.supervised_metrics or {}
            observed_mutations = metrics.get("mutation_events", 0) if metrics.get("task_id") == task["id"] else None
            verdict = agentsmith_supervision.classify_outcome(
                exit_code=exit_code,
                changed=changed,
                validation_ok=validation["ok"],
                bridge_events=bridge_events,
                observed_mutations=observed_mutations,
                assistant_text="\n".join(task.get("assistant_messages", [])),
                budget_exhausted=bool(task.get("budget_exhausted")),
            )
            action_only = verdict["action_only"]
            outcome = {
                "status": verdict["status"],
                "exit_code": exit_code,
                "changed": changed,
                "action_only": action_only,
                "bridge_events": bridge_events,
                "validation_ok": validation["ok"],
                "validation_errors": validation["errors"],
                "checkpoint": task["checkpoint"],
            }
            if task.get("budget_exhausted"):
                outcome["reason"] = task["budget_exhausted"] + (
                    "; the verified live document was kept" if verdict["budget_exhausted"]
                    else "; the unverified change was rolled back")
            follow_up = verdict["follow_up"]
            if action_only:
                # Nothing to commit or snapshot: just close the empty transaction.
                if doc.HasPendingTransaction:
                    doc.abortTransaction()
                    task["transaction_open"] = False
            elif follow_up == agentsmith_supervision.COMMIT_AND_SAVE:
                if doc.HasPendingTransaction:
                    doc.commitTransaction()
                    task["transaction_open"] = False
                doc.save()
                if not _fcstd_archive_valid(canonical):
                    doc = self._restore_task_snapshot(task, "post-save FCStd integrity check failed")
                    outcome.update({"status": "failed", "reason": "post-save FCStd integrity check failed", "restored": True})
                else:
                    with open(canonical, "rb") as handle:
                        outcome["file_sha256"] = hashlib.sha256(handle.read()).hexdigest()
                    outcome["after_checkpoint"] = self.server._checkpoint({"document": doc.Name, "label": "task-after-" + task["id"]})["path"]
                    try:
                        outcome["visual_after"] = self._create_visual_context(doc, task["id"], "after", task.get("capture_views"))
                    except Exception as exc:
                        outcome["visual_after_error"] = str(exc)
            elif follow_up == agentsmith_supervision.RESTORE_SNAPSHOT:
                doc = self._restore_task_snapshot(task, "backend result was not successfully verified")
                outcome["restored"] = True
            elif doc.HasPendingTransaction:
                doc.abortTransaction()
                doc.recompute()
                task["transaction_open"] = False
        outcome["guard_violation"] = task.get("guard_violation")
        outcome["budget_exhausted"] = bool(task.get("budget_exhausted"))
        outcome["rescue_checkpoint"] = task.get("rescue_checkpoint")
        outcome["canonical_path"] = canonical
        outcome["before_file_sha256"] = task["file_backup_sha256"]
        task_events = [event for event in self.server.events if event["seq"] > task["before_event_sequence"]]
        outcome["changed_objects"] = sorted(set(
            event.get("data", {}).get("object") for event in task_events
            if event.get("data", {}).get("object")
        ))
        outcome["response"] = "\n".join(task.get("assistant_messages", []))[-8000:]
        outcome["backend_output"] = "\n".join(task.get("raw_backend_output", []))[-12000:]
        outcome["checkpoint_before"] = task["checkpoint"]
        outcome["checkpoint_after"] = outcome.get("after_checkpoint")
        outcome["visual_before"] = task.get("visual_before")
        outcome["reference"] = task.get("reference")
        outcome["autofix_round"] = bool(task.get("autofix_round"))
        outcome.update({"task_id": task["id"], "backend": task.get("backend"), "model": task.get("model"), "prompt": task["prompt"], "started": task["started"], "finished": time.time()})
        project = self._project_directory()
        audit_dir = os.path.join(project, ".agentsmith")
        os.makedirs(audit_dir, exist_ok=True)
        with open(os.path.join(audit_dir, "task-history.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        self._append_document_history(canonical, outcome)
        if outcome["status"] == "success" and outcome.get("action_only"):
            message = "SUPERVISOR PASS: action-only task (slice/print/query) completed; document intentionally unchanged."
        elif outcome["status"] == "success" and outcome.get("budget_exhausted"):
            message = ("SUPERVISOR PASS (time budget exhausted): the backend was stopped after %d min, "
                       "but the live document changed, %d bridge events, geometry valid — kept and saved. "
                       "The backend's final report and checklist may be incomplete."
                       % (max(1, int(task.get("budget_seconds", LIVE_EDIT_BUDGET_SECONDS)) // 60),
                          outcome["bridge_events"]))
        elif outcome["status"] == "success":
            message = "SUPERVISOR PASS: live document changed, %d bridge events, geometry valid." % outcome["bridge_events"]
        else:
            message = "SUPERVISOR FAIL: no verified valid live-document change. " + json.dumps(outcome, ensure_ascii=False)
        self.codex_output.appendPlainText("\n" + message)
        self.codex_supervisor_status.setText(message)
        if outcome["status"] != "success":
            # A failed task is the other half of the lessons pipeline: the reviewer
            # never runs on one, so without this the richest failures — rolled back,
            # timed out, guard-tripped — would never reach lessons.md.
            self._offer_lesson(task.get("prompt", ""), message[:1500],
                               outcome.get("response", "")[-1500:])
        self.server.protected_documents.pop(task["document"], None)
        self.server.supervised_metrics = None
        self.codex_task = None
        self._refresh_document_history(True)
        return outcome
