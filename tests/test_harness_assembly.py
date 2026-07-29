"""Tests for harness prompt assembly (agentsmith_harness).

Includes a test against the REAL harness directory: the registry and the .md
files must stay consistent with each other, which is a cheap guard against a
playbook that is registered but missing (or vice versa).
"""

import json
import os
import shutil
import tempfile
import unittest

import helpers

import agentsmith_harness as harness

REAL_HARNESS = os.path.join(helpers.ADDON_DIR, "harness")


class FakeHarness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="harness-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.warnings = []

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def write_registry(self, registry):
        self.write("registry.json", json.dumps(registry))

    def test_sections_are_ordered_always_then_index_then_playbooks(self):
        self.write_registry({
            "always_include": ["core.md"],
            "playbooks": [{"id": "print3d", "file": "print3d.md",
                           "label": "3D print", "trigger": "the part is printed"}],
        })
        self.write("core.md", "CORE BODY")
        self.write("print3d.md", "PRINT BODY")
        label, text = harness.assemble_harness(self.dir, warn=self.warnings.append)
        self.assertEqual(label, "full harness (1 playbooks)")
        self.assertLess(text.index("CORE BODY"), text.index(harness.INDEX_HEADING))
        self.assertLess(text.index(harness.INDEX_HEADING), text.index("PRINT BODY"))
        self.assertIn("- **3D print** — apply when the part is printed", text)
        self.assertEqual(self.warnings, [])

    def test_playbook_without_trigger_still_appears(self):
        self.write_registry({"always_include": [],
                             "playbooks": [{"id": "x", "file": "x.md", "label": "Xko"}]})
        self.write("x.md", "X BODY")
        _, text = harness.assemble_harness(self.dir)
        self.assertIn("- **Xko**\n", text + "\n")
        self.assertNotIn("- **Xko** — apply when", text)

    def test_entries_missing_id_or_file_are_dropped(self):
        self.write_registry({"always_include": [], "playbooks": [
            {"id": "good", "file": "good.md"},
            {"id": "no-file"},
            {"file": "no-id.md"},
        ]})
        self.write("good.md", "GOOD")
        registry = harness.load_registry(self.dir)
        self.assertEqual([p["id"] for p in registry["playbooks"]], ["good"])

    def test_missing_registry_degrades_to_empty_with_a_warning(self):
        label, text = harness.assemble_harness(self.dir, warn=self.warnings.append)
        self.assertEqual(label, "full harness (0 playbooks)")
        self.assertEqual(text, "")
        self.assertEqual(len(self.warnings), 1)

    def test_broken_registry_json_degrades_instead_of_raising(self):
        self.write("registry.json", "{not json")
        registry = harness.load_registry(self.dir, warn=self.warnings.append)
        self.assertEqual(registry["playbooks"], [])
        self.assertEqual(len(self.warnings), 1)

    def test_unreadable_playbook_warns_but_keeps_the_rest(self):
        self.write_registry({"always_include": ["core.md"], "playbooks": [
            {"id": "gone", "file": "gone.md", "label": "Missing"},
        ]})
        self.write("core.md", "CORE BODY")
        _, text = harness.assemble_harness(self.dir, warn=self.warnings.append)
        self.assertIn("CORE BODY", text)
        self.assertIn("- **Missing**", text)  # index still lists it
        self.assertEqual(len(self.warnings), 1)

    def test_label_template_is_overridable_for_the_eval_runner(self):
        self.write_registry({"always_include": [], "playbooks": []})
        label, _ = harness.assemble_harness(self.dir, label_template="full harness (%d playbooks)")
        self.assertEqual(label, "full harness (0 playbooks)")

    def test_no_playbooks_means_no_index(self):
        self.write_registry({"always_include": ["core.md"], "playbooks": []})
        self.write("core.md", "CORE BODY")
        _, text = harness.assemble_harness(self.dir)
        self.assertNotIn(harness.INDEX_HEADING, text)


class RealHarness(unittest.TestCase):
    """The shipped harness must actually be assemblable."""

    def setUp(self):
        self.warnings = []
        self.registry = harness.load_registry(REAL_HARNESS, warn=self.warnings.append)

    def test_registry_loads_clean(self):
        self.assertEqual(self.warnings, [])
        self.assertTrue(self.registry["playbooks"])
        self.assertIn("core.md", self.registry["always_include"])
        self.assertIn("lessons.md", self.registry["always_include"])

    def test_every_registered_file_exists(self):
        names = list(self.registry["always_include"]) + [p["file"] for p in self.registry["playbooks"]]
        missing = [n for n in names if not os.path.isfile(os.path.join(REAL_HARNESS, n))]
        self.assertEqual(missing, [], "registry points at missing files: %s" % missing)

    def test_every_playbook_file_is_registered(self):
        registered = set(self.registry["always_include"])
        registered.update(p["file"] for p in self.registry["playbooks"])
        on_disk = {n for n in os.listdir(REAL_HARNESS) if n.endswith(".md")}
        # patterns/ holds on-demand micro-recipes indexed by patterns.md, not by
        # the registry, so only the top level is checked.
        self.assertEqual(on_disk - registered, set(),
                         "unregistered playbook files: %s" % (on_disk - registered))

    def test_assembles_without_warnings(self):
        label, text = harness.assemble_harness(REAL_HARNESS, warn=self.warnings.append)
        self.assertEqual(self.warnings, [])
        self.assertIn("playbook", label)
        self.assertIn(harness.INDEX_HEADING, text)
        self.assertGreater(len(text), 10000)

    def test_ids_are_unique(self):
        ids = [p["id"] for p in self.registry["playbooks"]]
        self.assertEqual(len(ids), len(set(ids)))




class ProjectToolRefreshTests(unittest.TestCase):
    """Which copies of the agent's tools get rewritten before a task.

    The agent runs from the project folder, and the slicer tools look for their
    config NEXT TO THEMSELVES. That makes a stale project copy invisible and
    authoritative at the same time: a config copied weeks ago kept telling a task
    that the printer had no PLA profile and no network address, hours after both
    had been configured. The task did the right thing and refused to print, which
    is how the shadowing stayed hidden -- nothing crashed, the answer was just
    quietly wrong.

    Hence the asymmetry below: executable tools go by timestamp, the config is
    always rewritten. A timestamp check cannot work for the config because the
    agent edits the project copy mid-run, which makes the stale file the newer
    one exactly when it matters most.
    """

    def test_a_missing_tool_is_always_copied(self):
        for name in harness.AGENT_TOOLS:
            self.assertTrue(harness.should_refresh(name, None, 100.0), name)

    def test_a_stale_tool_is_refreshed(self):
        self.assertTrue(harness.should_refresh("slice_check.py", 100.0, 200.0))

    def test_an_up_to_date_tool_is_left_alone(self):
        self.assertFalse(harness.should_refresh("slice_check.py", 200.0, 200.0))

    def test_a_locally_newer_tool_is_respected(self):
        self.assertFalse(harness.should_refresh("print_send.py", 300.0, 200.0))

    def test_the_config_is_refreshed_even_when_identical_in_age(self):
        self.assertTrue(harness.should_refresh("slicer-config.json", 200.0, 200.0))

    def test_the_config_is_refreshed_even_when_the_copy_is_newer(self):
        # The exact shape of the bug: the agent rewrote the project copy during a
        # run, so it was newer than the addon's, and a timestamp check would have
        # preserved the broken file forever.
        self.assertTrue(harness.should_refresh("slicer-config.json", 999.0, 100.0))

    def test_the_config_travels_with_the_tools(self):
        self.assertIn("slicer-config.json", harness.AGENT_TOOLS)
        for name in harness.ALWAYS_REFRESHED:
            self.assertIn(name, harness.AGENT_TOOLS)

    def test_the_bridge_client_ships_too(self):
        # A reviewer once burned budget hunting for it in sibling folders.
        self.assertIn("freecad_bridge_client.py", harness.AGENT_TOOLS)



if __name__ == "__main__":
    unittest.main()
