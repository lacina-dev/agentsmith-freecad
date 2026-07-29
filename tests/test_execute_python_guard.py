"""The execute_python blocklist must keep a supervised task inside its document.

Found live (2026-07-26): a worker created `toiletPaperHolder` via
`App.newDocument` mid-task. The new document opened another 3D window in the
user's face — and, worse, sat entirely outside the safety net: checkpoints,
rollback and the file guard all bind to the document that was active when the
task started. The blocklist is a substring scan over the submitted code, so
these tests read the source rather than importing bridge_server (which needs
FreeCAD).
"""

import os
import re
import unittest

import helpers


def forbidden_tuple():
    with open(os.path.join(helpers.ADDON_DIR, "bridge_server.py"), encoding="utf-8") as handle:
        source = handle.read()
    match = re.search(r"forbidden = \((.*?)\)", source, re.S)
    if not match:
        raise AssertionError("forbidden tuple not found in bridge_server.py")
    return match.group(1)


class ExecutePythonGuard(unittest.TestCase):
    def test_document_creation_is_forbidden(self):
        # ".newDocument(" (not "App.newDocument") so an aliased
        # `import FreeCAD as F; F.newDocument(...)` is caught too.
        self.assertIn('".newDocument("', forbidden_tuple())

    def test_document_lifecycle_still_forbidden(self):
        # The pre-existing guard this one extends; a refactor that rebuilds the
        # tuple and drops these would silently reopen the old holes.
        for needle in ('"App.closeDocument"', '"App.openDocument"', '".saveAs("'):
            self.assertIn(needle, forbidden_tuple())


if __name__ == "__main__":
    unittest.main()
