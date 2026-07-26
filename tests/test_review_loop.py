"""Tests for the review loop: convergence detection and the precheck report.

The loop used to be hard-wired to exactly one corrective round. These tests pin
the replacement: keep going while the findings change, stop when the reviewer
starts repeating itself, and always say why.
"""

import os
import unittest

import helpers  # noqa: F401

import agentsmith_review as review


CONCERNS = """VERDICT: CONCERNS
- The wall thickness measures 1.8 mm but the request says 2.0 mm.
- The mounting holes are 4.2 mm; M5 clearance needs 5.5 mm.
Suggested fixes: set WallT to 2.0 and BoltDia to 5.5.
"""

# Same two defects, re-measured after a failed fix attempt: the numbers moved a
# little, the complaint did not.
CONCERNS_REPEATED = """VERDICT: CONCERNS
- The wall thickness measures 1.9 mm but the request says 2.0 mm.
- The mounting holes are 4.4 mm; M5 clearance needs 5.5 mm.
"""

CONCERNS_DIFFERENT = """VERDICT: CONCERNS
- The chamfer on the bottom edge is missing entirely.
- The lid does not clear the box by the requested 0.25 mm.
"""

PASSED = "VERDICT: PASS\n- Everything matches the request.\n"


class Normalization(unittest.TestCase):
    def test_bullets_become_findings(self):
        self.assertEqual(len(review.normalize_findings(CONCERNS)), 2)

    def test_numbers_are_ignored_so_a_failed_fix_is_not_read_as_progress(self):
        self.assertEqual(review.normalize_findings(CONCERNS),
                         review.normalize_findings(CONCERNS_REPEATED))

    def test_suggested_fixes_line_is_not_a_finding(self):
        for finding in review.normalize_findings(CONCERNS):
            self.assertNotIn("suggested", finding)

    def test_prose_without_bullets_yields_nothing(self):
        self.assertEqual(review.normalize_findings("VERDICT: CONCERNS\nIt is wrong."), set())

    def test_empty_input_is_safe(self):
        self.assertEqual(review.normalize_findings(None), set())

    def test_similarity_bounds(self):
        findings = review.normalize_findings(CONCERNS)
        self.assertEqual(review.findings_similarity(findings, findings), 1.0)
        self.assertEqual(review.findings_similarity(
            findings, review.normalize_findings(CONCERNS_DIFFERENT)), 0.0)
        self.assertEqual(review.findings_similarity(set(), findings), 0.0)


class Decision(unittest.TestCase):
    def test_first_concerns_starts_a_round(self):
        decision = review.autofix_decision(CONCERNS)
        self.assertTrue(decision["run"])
        self.assertIn("round 1 of 2", decision["reason"])

    def test_a_pass_verdict_stops_the_loop(self):
        decision = review.autofix_decision(PASSED)
        self.assertFalse(decision["run"])
        self.assertIn("no concerns", decision["reason"])

    def test_new_findings_earn_another_round(self):
        decision = review.autofix_decision(
            CONCERNS_DIFFERENT, review.normalize_findings(CONCERNS), rounds_done=1)
        self.assertTrue(decision["run"])
        self.assertIn("findings changed", decision["reason"])

    def test_repeated_findings_stop_the_loop(self):
        # The real signal that another round is pointless.
        decision = review.autofix_decision(
            CONCERNS_REPEATED, review.normalize_findings(CONCERNS), rounds_done=1)
        self.assertFalse(decision["run"])
        self.assertTrue(decision["converged"])
        self.assertIn("repeating", decision["reason"])

    def test_round_limit_is_a_backstop_not_the_main_rule(self):
        decision = review.autofix_decision(
            CONCERNS_DIFFERENT, review.normalize_findings(CONCERNS), rounds_done=2)
        self.assertFalse(decision["run"])
        self.assertIn("limit reached", decision["reason"])
        self.assertFalse(decision["converged"])

    def test_disabled_and_busy_are_reported_distinctly(self):
        self.assertIn("off", review.autofix_decision(CONCERNS, enabled=False)["reason"])
        self.assertIn("running", review.autofix_decision(CONCERNS, busy=True)["reason"])

    def test_every_refusal_gives_a_reason(self):
        for decision in (review.autofix_decision(PASSED),
                         review.autofix_decision(CONCERNS, enabled=False),
                         review.autofix_decision(CONCERNS, busy=True),
                         review.autofix_decision(CONCERNS, rounds_done=9)):
            self.assertTrue(decision["reason"].strip())

    def test_single_round_configuration_reproduces_the_old_behaviour(self):
        first = review.autofix_decision(CONCERNS, max_rounds=1)
        self.assertTrue(first["run"])
        second = review.autofix_decision(CONCERNS_DIFFERENT, first["findings"],
                                         rounds_done=1, max_rounds=1)
        self.assertFalse(second["run"])


class PrecheckReport(unittest.TestCase):
    RESULTS = {
        "validate": {"ok": True},
        "check_solid": {"objects": [{"name": "Hook", "solid_count": 1,
                                     "is_valid": True, "is_closed": True}]},
        "model_digest": {"overall_bounding_box": {"min": [0, 0, 0], "max": [60, 38, 120]},
                         "spreadsheets": {"Parameters": {"B1": {"alias": "WallT",
                                                                "value": {"value": 8.0}}}}},
        "feature_probe": {"objects": [{
            "object": "Hook",
            "largest_plane": {"normal": [0.0, -1.0, 0.0], "standoff_mm": 38.0},
            "holes": [{"diameter_mm": 5.5, "axis": [0.0, 1.0, 0.0],
                       "angle_deg": 270.0, "kind": "hole"},
                      {"diameter_mm": 24.0, "axis": [1.0, 0.0, 0.0],
                       "angle_deg": 90.0, "kind": "boss"}]}]},
        "print_readiness": {"objects": [{"object": "Hook", "overhang_area_pct": 2.1,
                                         "min_bbox_dim": 8.0}]},
    }

    def report(self, **overrides):
        results = dict(self.RESULTS)
        results.update(overrides)
        return review.format_precheck_report(results)

    def test_reports_the_numbers_a_reviewer_would_have_to_fetch_by_hand(self):
        text = self.report()
        self.assertIn("validate: OK", text)
        self.assertIn("1 solid(s)", text)
        self.assertIn("[60, 38, 120]", text)
        self.assertIn("WallT=8.0", text)
        self.assertIn("stands off it by 38.0 mm", text)
        self.assertIn("overhang area 2.1%", text)

    def test_only_real_bores_are_listed(self):
        text = self.report()
        self.assertIn("d=5.5 mm", text)
        self.assertNotIn("d=24", text)  # that one is a boss, not a hole

    def test_validation_failure_carries_its_errors(self):
        text = self.report(validate={"ok": False, "errors": ["Pad: broken"]})
        self.assertIn("FAILED", text)
        self.assertIn("Pad: broken", text)

    def test_a_command_that_could_not_run_is_stated_not_hidden(self):
        # Silence would read as "fine", which is the opposite of the truth.
        text = self.report(print_readiness=RuntimeError("unknown command"))
        self.assertIn("print_readiness: could not run", text)

    def test_probe_errors_on_one_object_do_not_break_the_report(self):
        text = self.report(feature_probe={"objects": [{"object": "Bad", "error": "boom"}]})
        self.assertIn("validate: OK", text)

    def test_nothing_measurable_says_so(self):
        self.assertIn("no deterministic checks", review.format_precheck_report({}))



class PanelWiring(unittest.TestCase):
    """The GUI file is not importable without FreeCAD, so check the wiring in source.

    These are cheap guards against the loop silently reverting to its old shape —
    the kind of regression that only shows up as "why did it stop after one round?"
    """

    @classmethod
    def setUpClass(cls):
        cls.source = helpers.gui_source()

    def test_autofix_uses_the_shared_decision_function(self):
        self.assertIn("agentsmith_review.autofix_decision", self.source)

    def test_the_one_shot_guard_is_gone(self):
        # The old rule ("never chains") was a hard stop after exactly one round.
        self.assertNotIn('if not state or state.get("autofix_round"):', self.source)

    def test_a_new_user_task_resets_the_corrective_chain(self):
        self.assertIn("self._autofix_chain = None", self.source)

    def test_escalation_replaces_the_model_for_the_corrective_round(self):
        self.assertIn("_autofix_model", self.source)
        self.assertIn('model_label = "%s (eskalace)" % escalated', self.source)

    def test_reviewer_gets_the_precheck_report(self):
        self.assertIn("format_precheck_report(self._run_prechecks())", self.source)
        self.assertIn("ALREADY MEASURED FOR YOU", self.source)

    def test_reviewer_is_told_not_to_re_measure(self):
        self.assertIn("do NOT spend your budget re-fetching them", self.source)

if __name__ == "__main__":
    unittest.main()
