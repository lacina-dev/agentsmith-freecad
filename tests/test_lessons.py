"""Tests for the lessons pipeline (agentsmith_lessons).

lessons.md is treated as binding by the modeling agent, so the two properties that
matter most here are: a malformed or invented entry must be caught before a human
is asked to approve it, and numbering must never collide with a rule that other
playbooks already cite.
"""

import os
import unittest

import helpers  # noqa: F401

import agentsmith_lessons as lessons

REAL_LESSONS = os.path.join(helpers.ADDON_DIR, "harness", "lessons.md")

SAMPLE = """# Lessons from real failures

Intro paragraph.

## L1 — Functional pose before geometry (2026-07-18, wall hook)
A hook was modeled flat. **Rule:** state the mounting surface before modeling.

## L2 — Map a reference image to its projection (2026-07-18, coat hook)
A photo was rebuilt in the wrong view. **Rule:** decide which view each reference shows.
"""

GOOD_ENTRY = ("## L6 — Press fits are modelled undersize (2026-07-25, 608 pillow block)\n"
              "The bore came out at the nominal 22.0 mm, which is a slip fit, twice in a "
              "row. **Rule:** when a task asks for a press or interference fit, model the "
              "bore undersize and put the interference in the Spreadsheet as its own "
              "parameter so it is visible and checkable.")


class Parsing(unittest.TestCase):
    def test_entries_are_parsed_in_order(self):
        entries = lessons.parse_entries(SAMPLE)
        self.assertEqual([entry["number"] for entry in entries], [1, 2])
        self.assertIn("Functional pose", entries[0]["title"])

    def test_body_excludes_the_next_heading(self):
        self.assertNotIn("L2", lessons.parse_entries(SAMPLE)[0]["body"])

    def test_empty_file_parses_to_nothing(self):
        self.assertEqual(lessons.parse_entries(""), [])
        self.assertEqual(lessons.next_number(""), 1)

    def test_next_number_follows_the_highest_not_the_count(self):
        # Entries are cited by number from the playbooks, so a gap left by a
        # promoted entry must never be handed out again.
        gapped = SAMPLE + "\n## L7 — Something (2026-07-20, x)\nBody. **Rule:** do the thing well.\n"
        self.assertEqual(lessons.next_number(gapped), 8)

    def test_the_real_lessons_file_parses(self):
        with open(REAL_LESSONS, encoding="utf-8") as handle:
            text = handle.read()
        entries = lessons.parse_entries(text)
        self.assertGreaterEqual(len(entries), 5)
        self.assertEqual([entry["number"] for entry in entries],
                         sorted(entry["number"] for entry in entries))
        for entry in entries:
            with self.subTest(entry=entry["number"]):
                self.assertIn(lessons.RULE_MARKER, entry["body"])


class Validation(unittest.TestCase):
    def test_a_well_formed_entry_has_no_problems(self):
        self.assertEqual(lessons.validate_entry(GOOD_ENTRY, SAMPLE), [])

    def test_empty_is_rejected(self):
        self.assertEqual(lessons.validate_entry("", SAMPLE), ["the entry is empty"])

    def test_missing_heading(self):
        problems = lessons.validate_entry("Some prose. **Rule:** always do the right thing.",
                                          SAMPLE)
        self.assertTrue(any("heading" in problem for problem in problems))

    def test_number_collision_names_the_free_number(self):
        colliding = GOOD_ENTRY.replace("L6", "L2")
        problems = lessons.validate_entry(colliding, SAMPLE)
        self.assertTrue(any("L2 already exists" in problem for problem in problems))
        self.assertTrue(any("L3" in problem for problem in problems))

    def test_missing_date(self):
        undated = GOOD_ENTRY.replace("(2026-07-25, 608 pillow block)", "(pillow block)")
        problems = lessons.validate_entry(undated, SAMPLE)
        self.assertTrue(any("ISO date" in problem for problem in problems))

    def test_an_entry_without_a_rule_is_an_anecdote(self):
        anecdote = "## L6 — Something odd (2026-07-25, part)\nIt broke and we fixed it."
        problems = lessons.validate_entry(anecdote, SAMPLE)
        self.assertTrue(any("anecdote" in problem for problem in problems))

    def test_a_rule_too_short_to_act_on(self):
        stub = "## L6 — Title (2026-07-25, part)\nIt broke. **Rule:** be careful."
        self.assertTrue(any("too short" in problem
                            for problem in lessons.validate_entry(stub, SAMPLE)))

    def test_missing_incident_description(self):
        ruleonly = "## L6 — Title (2026-07-25, part)\n**Rule:** always undersize a press fit bore."
        problems = lessons.validate_entry(ruleonly, SAMPLE)
        self.assertTrue(any("what actually happened" in problem for problem in problems))

    def test_overlong_entries_are_pushed_towards_a_playbook(self):
        bloated = GOOD_ENTRY + (" Additional detail." * 100)
        self.assertTrue(any("playbook" in problem
                            for problem in lessons.validate_entry(bloated, SAMPLE)))


class Appending(unittest.TestCase):
    def test_entry_is_appended_with_one_blank_line(self):
        result = lessons.append_entry(SAMPLE, GOOD_ENTRY)
        self.assertTrue(result.endswith(GOOD_ENTRY + "\n"))
        self.assertIn("projection.\n\n## L6", result.replace("shows.", "projection."))

    def test_appending_keeps_every_existing_entry(self):
        result = lessons.append_entry(SAMPLE, GOOD_ENTRY)
        self.assertEqual([e["number"] for e in lessons.parse_entries(result)], [1, 2, 6])

    def test_appending_nothing_changes_nothing(self):
        self.assertEqual(lessons.append_entry(SAMPLE, "   "), SAMPLE)


class Drafting(unittest.TestCase):
    def test_prompt_carries_the_number_date_and_evidence(self):
        prompt = lessons.build_prompt(9, "Model a bracket", "failed: no mutation",
                                      "VERDICT: CONCERNS", "2026-07-25")
        self.assertIn("## L9", prompt)
        self.assertIn("2026-07-25", prompt)
        self.assertIn("Model a bracket", prompt)
        self.assertIn("VERDICT: CONCERNS", prompt)

    def test_prompt_permits_declining(self):
        # A pipeline that must produce a rule every time will invent one, and an
        # invented rule lands in a file the modeling agent treats as binding.
        self.assertIn("NO LESSON", lessons.build_prompt(1, "t", "o", "v", "2026-07-25"))

    def test_extract_plain_entry(self):
        entry, declined = lessons.extract_entry(GOOD_ENTRY)
        self.assertFalse(declined)
        self.assertTrue(entry.startswith("## L6"))

    def test_extract_strips_preamble_and_code_fences(self):
        wrapped = "Sure, here it is:\n\n```markdown\n%s\n```\n" % GOOD_ENTRY
        entry, declined = lessons.extract_entry(wrapped)
        self.assertFalse(declined)
        self.assertTrue(entry.startswith("## L6"))
        self.assertNotIn("```", entry)

    def test_a_refusal_is_a_valid_answer(self):
        entry, declined = lessons.extract_entry(
            "NO LESSON — the failure was a grader bug, not a modelling mistake.")
        self.assertTrue(declined)

    def test_empty_output_is_not_a_refusal(self):
        entry, declined = lessons.extract_entry("")
        self.assertEqual(entry, "")
        self.assertFalse(declined)

    def test_drafted_entries_survive_validation(self):
        entry, _ = lessons.extract_entry("Here you go:\n\n%s" % GOOD_ENTRY)
        self.assertEqual(lessons.validate_entry(entry, SAMPLE), [])



class PanelWiring(unittest.TestCase):
    """Source-level guards: BridgeGui needs FreeCAD, so it cannot be imported here."""

    @classmethod
    def setUpClass(cls):
        cls.source = helpers.gui_source()

    def test_both_failure_paths_offer_a_lesson(self):
        # A reviewer verdict is only half the story: the reviewer never runs on a
        # failed task, so the richest failures would otherwise never be recorded.
        self.assertIn("if agentsmith_review.VERDICT_CONCERNS in (verdict_text or \"\"):", self.source)
        self.assertIn('if outcome["status"] != "success":', self.source)

    def test_writing_requires_an_explicit_confirmation(self):
        self.assertIn("QtWidgets.QMessageBox.Save", self.source)
        self.assertIn("Lesson not recorded (cancelled)", self.source)

    def test_the_default_button_is_cancel(self):
        # The destructive default would be a rule nobody chose landing in a binding file.
        self.assertIn("dialog.setDefaultButton(QtWidgets.QMessageBox.Cancel)", self.source)

    def test_validation_problems_are_shown_before_the_user_decides(self):
        self.assertIn("agentsmith_lessons.validate_entry(entry, existing)", self.source)
        self.assertIn("Formal objections", self.source)

    def test_a_declined_draft_writes_nothing(self):
        self.assertIn("if declined:", self.source)

    def test_harness_cache_is_dropped_after_a_write(self):
        # Otherwise the next task would still be prompted with the pre-write harness.
        self.assertIn("self._harness_registry_cache = None", self.source)

if __name__ == "__main__":
    unittest.main()
