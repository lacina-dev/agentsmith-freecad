"""GUI smoke test — the part of the addon that unit tests cannot reach.

Run it inside FreeCAD, not with unittest:

    ~/AppImages/freecad.appimage tests/gui_smoke.py
    cat /tmp/agentsmith-gui-smoke.log

Everything in tests/ runs without FreeCAD by design; this file is the deliberate
exception. It covers what that split leaves uncovered: that the panel actually
constructs, that the modules it was split into fit back together, and that the
bridge starts, changes access level, takes a checkpoint and stops again.

It does NOT run a modeling task — that needs a backend, real tokens and minutes.
The task path is verified by eval/run_e2e.py and by using the thing.

Written after BridgeGui.py was split into four modules: a refactor that compiles
and passes every unit test can still leave a panel that dies on construction, and
finding that out by opening FreeCAD by hand is not a check anybody repeats.
"""

import os
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore

LOG = "/tmp/agentsmith-gui-smoke.log"
PORT = 18499                      # not the default, so a live session is untouched


def log(message):
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(str(message) + "\n")


class Smoke(object):
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, call, expect=None):
        try:
            value = call()
        except Exception as exc:
            self.failed += 1
            log("  FAIL  %-34s %s: %s" % (name, type(exc).__name__, exc))
            return None
        if expect is not None and value != expect:
            self.failed += 1
            log("  FAIL  %-34s got %r, wanted %r" % (name, value, expect))
            return value
        self.passed += 1
        log("  ok    %-34s %r" % (name, value))
        return value


def run():
    smoke = Smoke()
    panel = None
    try:
        import BridgeGui
        import bridge_server
        import printer_panel
        import task_supervisor

        log("--- modules")
        smoke.check("bridge version", lambda: bridge_server.BRIDGE_VERSION)
        smoke.check("task budget (s)", lambda: task_supervisor.LIVE_EDIT_BUDGET_SECONDS)
        smoke.check("monitor interval (ms)", lambda: printer_panel.MONITOR_INTERVAL_MS)

        log("--- panel constructs")
        panel = BridgeGui.BridgePanel()
        panel.show()
        smoke.check("mro", lambda: [c.__name__ for c in type(panel).__mro__[:3]],
                    ["BridgePanel", "TaskSupervisionMixin", "QWidget"])
        smoke.check("every mixin method bound", lambda: [
            name for name in dir(task_supervisor.TaskSupervisionMixin)
            if name.startswith("_") and not hasattr(panel, name)], [])

        log("--- panel methods across module boundaries")
        project = smoke.check("project directory", panel._project_directory)
        # _harness_registry returns the parsed registry dict; _harness_text returns
        # a (label, text) pair. Both were asserted wrongly here at first — the
        # tuple's length two read as "the harness assembled to two characters".
        smoke.check("harness playbooks",
                    lambda: len(panel._harness_registry().get("playbooks", [])) >= 18, True)
        smoke.check("harness text assembles",
                    lambda: len(panel._harness_text()[1]) > 10000, True)
        smoke.check("printer context", lambda: panel._printer_context() is not None, True)
        smoke.check("lessons path", lambda: os.path.basename(panel._lessons_path()),
                    "lessons.md")
        smoke.check("models for claude", lambda: len(panel._models_for_backend("claude")) > 0,
                    True)
        # Returns None while the MCP checkbox is off, which is the default state.
        smoke.check("mcp settings", lambda: panel._mcp_settings(project) is None, True)
        smoke.check("history reads", lambda: isinstance(panel._effective_history([]), list),
                    True)
        smoke.check("capture views", lambda: len(panel._selected_capture_views()) > 0, True)
        # _state only repaints labels; the check is that it runs and the text lands.
        smoke.check("state label", lambda: (panel._state(False, PORT),
                                            panel.status.text())[1], "Stopped")

        log("--- bridge lifecycle")
        smoke.check("starts", lambda: (panel.server.start(PORT),
                                       panel.server.server.isListening())[1], True)
        smoke.check("discovery file written",
                    lambda: os.path.isfile(bridge_server.DISCOVERY_FILE), True)
        for level in ("read", "edit", "python"):
            smoke.check("access level -> %s" % level,
                        lambda level=level: (panel.access.setCurrentText(level.capitalize())
                                             or True) is not None, True)
        smoke.check("stops", lambda: (panel.server.stop(),
                                      panel.server.server.isListening())[1], False)

        log("--- document operations")
        doc = App.newDocument("gui_smoke")
        try:
            box = doc.addObject("Part::Box", "Box")
            doc.recompute()
            smoke.check("checkpoint", lambda: panel._checkpoint() is not False)
            smoke.check("validate", lambda: panel._validate() is not False)
            smoke.check("undo", lambda: panel._undo() is not False)
            smoke.check("redo", lambda: panel._redo() is not False)
        finally:
            App.closeDocument(doc.Name)
    except Exception:
        smoke.failed += 1
        log("FAILED during setup:\n" + traceback.format_exc())
    finally:
        if panel is not None:
            try:
                panel.close()
            except Exception:
                pass

    log("\n%d ok, %d failed" % (smoke.passed, smoke.failed))
    log("RESULT: %s" % ("PASS" if smoke.failed == 0 else "FAIL"))
    QtCore.QTimer.singleShot(700, Gui.getMainWindow().close)


if os.path.exists(LOG):
    os.unlink(LOG)
QtCore.QTimer.singleShot(2500, run)
