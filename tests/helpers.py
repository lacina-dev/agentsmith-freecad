"""Shared test setup: make the addon's GUI-free modules importable.

The addon is a FreeCAD Mod directory, not a Python package — FreeCAD puts each
Mod directory on sys.path and the addon imports its modules flat (``import
BridgeGui``). Tests do the same, so they exercise exactly the import shape that
runs inside FreeCAD.

Only modules that do NOT import FreeCAD/Qt can be loaded this way; that is the
point of the split, and test_no_gui_imports guards it.
"""

import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
ADDON_DIR = os.path.join(REPO_ROOT, "freecad_bridge")
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")

if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)


def load_fixture_events(name):
    """Parse one JSONL fixture of real captured backend output."""
    import json
    path = os.path.join(FIXTURES_DIR, name)
    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


#: The addon's GUI-side modules. Some tests assert that a behaviour is wired into
#: the panel by reading the source, because the wiring itself cannot be imported
#: without FreeCAD. They search all of these rather than one filename: the panel
#: was a single 4000-line module until it was split, and tests that named the
#: file broke on a refactor that changed nothing they were actually checking.
GUI_MODULES = ("BridgeGui.py", "bridge_server.py", "task_supervisor.py",
               "printer_panel.py")


def gui_source():
    """Concatenated source of the GUI modules, for wiring assertions."""
    import os
    chunks = []
    for name in GUI_MODULES:
        path = os.path.join(ADDON_DIR, name)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                chunks.append(handle.read())
    return "\n".join(chunks)

