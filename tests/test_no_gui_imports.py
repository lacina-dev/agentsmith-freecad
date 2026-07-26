"""Guard: the extracted modules must stay importable without FreeCAD.

The whole point of the split is that this logic can be tested (and reused by the
eval runner) in a plain interpreter. If someone reaches for `import FreeCAD` in
one of these modules, the tests stop being runnable outside FreeCAD — so that
regression is caught here rather than by a confusing ImportError months later.
"""

import ast
import os
import unittest

import helpers

# Modules that must never touch the FreeCAD or Qt runtime.
GUI_FREE_MODULES = (
    "agentsmith_supervision.py",
    "agentsmith_backends.py",
    "agentsmith_harness.py",
)

FORBIDDEN_ROOTS = {"FreeCAD", "FreeCADGui", "Part", "PySide", "PySide2", "PySide6", "pivy"}


def imported_roots(path):
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


class NoGuiImports(unittest.TestCase):
    def test_modules_declare_no_freecad_or_qt_imports(self):
        for name in GUI_FREE_MODULES:
            path = os.path.join(helpers.ADDON_DIR, name)
            with self.subTest(module=name):
                offenders = imported_roots(path) & FORBIDDEN_ROOTS
                self.assertEqual(offenders, set(),
                                 "%s imports %s — it must stay GUI-free" % (name, offenders))

    def test_modules_import_cleanly_here(self):
        # This test process has no FreeCAD; a successful import proves the point.
        import agentsmith_backends  # noqa: F401
        import agentsmith_harness  # noqa: F401
        import agentsmith_supervision  # noqa: F401


if __name__ == "__main__":
    unittest.main()
