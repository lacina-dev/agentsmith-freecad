"""Tests for the supervisor's decision logic (agentsmith_supervision).

These encode the rules that decide whether a user's model change is kept or
rolled back, so they are the tightest safety net in the repo. Several cases come
from failures observed live — they are marked as such.
"""

import unittest

import helpers  # noqa: F401  (puts the addon dir on sys.path)

import agentsmith_supervision as sup


MARKER = sup.ACTION_ONLY_MARKER


def classify(**overrides):
    """A normal, fully successful modeling run, with fields overridable."""
    args = {
        "exit_code": 0,
        "changed": True,
        "validation_ok": True,
        "bridge_events": 7,
        "observed_mutations": 3,
        "assistant_text": "Done, I changed WallThickness to 2.4 mm.",
    }
    args.update(overrides)
    return sup.classify_outcome(**args)


class ClassifySuccess(unittest.TestCase):
    def test_all_evidence_present_is_success(self):
        verdict = classify()
        self.assertEqual(verdict["status"], "success")
        self.assertFalse(verdict["action_only"])
        self.assertEqual(verdict["follow_up"], sup.COMMIT_AND_SAVE)

    def test_nonzero_exit_is_never_success(self):
        # With a verified document it is "interrupted" (kept), never "success".
        self.assertNotEqual(classify(exit_code=1)["status"], "success")
        self.assertEqual(classify(exit_code=1, validation_ok=False)["status"], "failed")

    def test_invalid_geometry_is_never_success(self):
        self.assertEqual(classify(validation_ok=False)["status"], "failed")

    def test_no_bridge_events_is_never_success(self):
        # The backend may have edited a generation script instead of the live
        # document; without a single bridge call nothing was really delivered.
        self.assertEqual(classify(bridge_events=0)["status"], "failed")

    def test_unchanged_document_is_never_success(self):
        self.assertEqual(classify(changed=False)["status"], "failed")


class ClassifyRollback(unittest.TestCase):
    def test_unverified_but_real_change_is_rolled_back(self):
        verdict = classify(exit_code=1, validation_ok=False, observed_mutations=2)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["follow_up"], sup.RESTORE_SNAPSHOT)

    def test_failed_run_that_touched_nothing_just_aborts(self):
        # Nothing to roll back — restoring would needlessly reopen the document.
        verdict = classify(exit_code=1, changed=False, observed_mutations=0)
        self.assertEqual(verdict["follow_up"], sup.ABORT_TRANSACTION)

    def test_missing_mutation_metrics_are_treated_as_unsafe(self):
        # Metrics belonging to another task (None) must not be read as "nothing
        # happened": with a changed fingerprint we roll back rather than trust it.
        verdict = classify(exit_code=1, observed_mutations=None)
        self.assertEqual(verdict["follow_up"], sup.RESTORE_SNAPSHOT)


class ClassifyInterrupted(unittest.TestCase):
    """The backend died with a non-zero exit but left a verified document.

    Observed live (2026-09-08, second toilet-roll holder): model built, sliced,
    report written, then the provider's usage limit killed the backend on the
    last step (exit 1). The finished model was rolled back for an exit code.
    """

    def test_verified_document_survives_a_backend_crash(self):
        verdict = classify(exit_code=1)
        self.assertEqual(verdict["status"], "interrupted")
        self.assertTrue(verdict["interrupted"])
        self.assertEqual(verdict["follow_up"], sup.COMMIT_AND_SAVE)

    def test_interrupted_is_not_success(self):
        # The reviewer and the "Done · verified" state are for clean finishes.
        self.assertNotEqual(classify(exit_code=1)["status"], "success")

    def test_invalid_geometry_after_a_crash_is_rolled_back(self):
        verdict = classify(exit_code=1, validation_ok=False)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["follow_up"], sup.RESTORE_SNAPSHOT)

    def test_crash_with_no_observed_mutation_is_not_kept(self):
        verdict = classify(exit_code=1, observed_mutations=0)
        self.assertEqual(verdict["status"], "failed")

    def test_crash_with_foreign_metrics_is_still_rolled_back(self):
        # Metrics from another task (None) are not evidence for keeping anything.
        verdict = classify(exit_code=1, observed_mutations=None)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["follow_up"], sup.RESTORE_SNAPSHOT)

    def test_crash_that_changed_nothing_just_aborts(self):
        verdict = classify(exit_code=1, changed=False, observed_mutations=0)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["follow_up"], sup.ABORT_TRANSACTION)

    def test_clean_finishes_are_not_interrupted(self):
        self.assertFalse(classify()["interrupted"])
        self.assertFalse(classify(exit_code=-1, budget_exhausted=True)["interrupted"])


class ClassifyBudgetExhausted(unittest.TestCase):
    """The watchdog stopped the backend because time ran out.

    Observed live (2026-09-08): a backend finished and validated a toilet-roll
    holder after ~11 minutes, sliced it, then spent the rest of a 15-minute
    budget fetching anchor load tables for the write-up. The watchdog killed it
    and the supervisor rolled the finished model back to an empty document.
    """

    def test_verified_document_is_kept_after_timeout(self):
        verdict = classify(exit_code=-1, budget_exhausted=True)
        self.assertEqual(verdict["status"], "success")
        self.assertEqual(verdict["follow_up"], sup.COMMIT_AND_SAVE)
        self.assertTrue(verdict["budget_exhausted"])

    def test_timeout_without_the_flag_is_not_a_success(self):
        # The flag is what waives the clean-exit rule; a plain crash with a
        # verified document is "interrupted" (kept), never "success".
        verdict = classify(exit_code=-1)
        self.assertEqual(verdict["status"], "interrupted")
        self.assertEqual(verdict["follow_up"], sup.COMMIT_AND_SAVE)
        self.assertFalse(verdict["budget_exhausted"])

    def test_invalid_geometry_after_timeout_is_rolled_back(self):
        verdict = classify(exit_code=-1, validation_ok=False, budget_exhausted=True)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["follow_up"], sup.RESTORE_SNAPSHOT)

    def test_unchanged_document_after_timeout_is_not_a_success(self):
        verdict = classify(exit_code=-1, changed=False, observed_mutations=0, budget_exhausted=True)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["follow_up"], sup.ABORT_TRANSACTION)

    def test_fingerprint_jitter_alone_does_not_keep_a_timed_out_document(self):
        # Changed fingerprint but zero observed mutations: nothing real to keep.
        verdict = classify(exit_code=-1, observed_mutations=0, budget_exhausted=True)
        self.assertEqual(verdict["status"], "failed")

    def test_timeout_with_no_bridge_events_is_not_a_success(self):
        verdict = classify(exit_code=-1, bridge_events=0, budget_exhausted=True)
        self.assertEqual(verdict["status"], "failed")

    def test_clean_exit_at_the_edge_is_an_ordinary_success(self):
        verdict = classify(exit_code=0, budget_exhausted=True)
        self.assertEqual(verdict["status"], "success")
        self.assertTrue(verdict["budget_exhausted"])

    def test_normal_success_does_not_carry_the_flag(self):
        self.assertFalse(classify()["budget_exhausted"])


class BudgetExhaustionRecognition(unittest.TestCase):
    def test_the_watchdogs_own_message_is_recognised(self):
        violation, _, _ = sup.evaluate_guard(elapsed_seconds=901, budget_seconds=900,
                                             rss_growth_bytes=0, missing_checks=0)
        self.assertTrue(sup.is_budget_exhaustion(violation))

    def test_every_budget_size_is_recognised(self):
        for minutes in (1, 8, 15, 25, 30, 45, 60):
            self.assertTrue(sup.is_budget_exhaustion(sup.BUDGET_EXHAUSTED_TEMPLATE % minutes))

    def test_other_guard_findings_are_not(self):
        for reason in ("FreeCAD memory grew by more than 1 GiB during the task",
                       "Backend closed the protected FreeCAD document",
                       "Backend changed the protected document path",
                       "Task exceeded its budget", "", None):
            self.assertFalse(sup.is_budget_exhaustion(reason), reason)

    def test_a_document_violation_still_outranks_the_budget(self):
        # When the document itself is broken the finding is not a mere timeout.
        violation, _, _ = sup.evaluate_guard(elapsed_seconds=901, budget_seconds=900,
                                             rss_growth_bytes=0, missing_checks=0,
                                             document_missing=True)
        self.assertFalse(sup.is_budget_exhaustion(violation))


class BudgetNotice(unittest.TestCase):
    """The countdown the bridge attaches to every response during a task."""

    def notice(self, elapsed, budget=1500):
        return sup.budget_notice(elapsed, budget)

    def test_early_in_the_task_there_is_no_notice(self):
        info = self.notice(60)
        self.assertEqual(info["phase"], "working")
        self.assertIsNone(info["notice"])
        self.assertEqual(info["remaining_seconds"], 1440)
        self.assertEqual(info["elapsed_seconds"], 60)

    def test_half_time_asks_for_verified_geometry(self):
        info = self.notice(800)
        self.assertEqual(info["phase"], "half")
        self.assertIn("HALF-TIME", info["notice"])

    def test_last_quarter_forbids_research_and_starts_the_wrap_up(self):
        info = self.notice(1200)
        self.assertEqual(info["phase"], "wrap_up")
        self.assertIn("No web search/fetch", info["notice"])
        self.assertIn("save", info["notice"])

    def test_final_minutes_demand_save_and_the_final_message(self):
        info = self.notice(1420)
        self.assertEqual(info["phase"], "final")
        self.assertIn("save", info["notice"])

    def test_final_phase_is_at_least_two_minutes_even_on_a_short_budget(self):
        # 8-minute budget: 10 % would be 48 s, the floor is 120 s. On a budget
        # this short the quarter-mark and the floor coincide, so "half" hands
        # over to "final" directly — the last call still comes 2 min early.
        self.assertEqual(self.notice(370, budget=480)["phase"], "final")
        self.assertEqual(self.notice(350, budget=480)["phase"], "half")

    def test_expired_says_so(self):
        info = self.notice(1501)
        self.assertEqual(info["phase"], "expired")
        self.assertEqual(info["remaining_seconds"], -1)
        self.assertIn("EXPIRED", info["notice"])

    def test_phases_are_monotonic_over_the_whole_budget(self):
        order = ["working", "half", "wrap_up", "final", "expired"]
        seen = [self.notice(t)["phase"] for t in range(0, 1600, 10)]
        ranks = [order.index(phase) for phase in seen]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(sorted(set(seen), key=order.index), order)

    def test_degenerate_budget_does_not_divide_by_zero(self):
        self.assertEqual(self.notice(5, budget=0)["phase"], "expired")


class ClassifyActionOnly(unittest.TestCase):
    """Slice/print/query tasks legitimately change nothing."""

    def test_marker_with_no_mutation_is_accepted(self):
        verdict = classify(changed=False, observed_mutations=0,
                           bridge_events=2, assistant_text=MARKER + ": sliced for core_one")
        self.assertEqual(verdict["status"], "success")
        self.assertTrue(verdict["action_only"])
        self.assertEqual(verdict["follow_up"], sup.ABORT_TRANSACTION)

    def test_fingerprint_jitter_does_not_defeat_the_marker(self):
        # Observed live: a successful slice-only task read as "changed" because
        # Shape.hashCode() jitters when still-touched features rebuild on
        # recompute. Bridge events come only from the document observer, so a
        # task that merely read and sliced produces none — and the observer's
        # mutation count, not the fingerprint, is what decides.
        verdict = classify(changed=True, observed_mutations=0, bridge_events=0,
                           assistant_text=MARKER + ": sent to QIDI")
        self.assertTrue(verdict["action_only"])
        self.assertEqual(verdict["status"], "success")
        self.assertEqual(verdict["follow_up"], sup.ABORT_TRANSACTION)

    def test_marker_never_downgrades_a_genuine_success(self):
        # A run with real mutations and clean validation is a normal success; the
        # marker is not consulted at all, so it cannot skip the save or reviewer.
        verdict = classify(assistant_text=MARKER + ": also modeled something")
        self.assertFalse(verdict["action_only"])
        self.assertEqual(verdict["follow_up"], sup.COMMIT_AND_SAVE)

    def test_real_mutation_with_marker_is_not_action_only(self):
        # A worker that mutated the model may not claim the action-only exemption.
        verdict = classify(exit_code=1, changed=True, observed_mutations=4,
                           assistant_text=MARKER + ": lying about it")
        self.assertFalse(verdict["action_only"])
        self.assertNotEqual(verdict["status"], "success")
        # ...and with geometry that does not validate the mutation is rolled back.
        verdict = classify(exit_code=1, changed=True, observed_mutations=4, validation_ok=False,
                           assistant_text=MARKER + ": lying about it")
        self.assertFalse(verdict["action_only"])
        self.assertEqual(verdict["follow_up"], sup.RESTORE_SNAPSHOT)

    def test_marker_requires_clean_exit(self):
        verdict = classify(exit_code=2, changed=False, observed_mutations=0,
                           assistant_text=MARKER + ": crashed afterwards")
        self.assertFalse(verdict["action_only"])
        self.assertEqual(verdict["status"], "failed")

    def test_marker_requires_valid_geometry(self):
        verdict = classify(changed=False, validation_ok=False, observed_mutations=0,
                           assistant_text=MARKER + ": but the model is broken")
        self.assertFalse(verdict["action_only"])

    def test_no_marker_means_no_exemption(self):
        verdict = classify(changed=False, observed_mutations=0,
                           assistant_text="Sliced it, done.")
        self.assertFalse(verdict["action_only"])
        self.assertEqual(verdict["status"], "failed")

    def test_empty_assistant_text_is_safe(self):
        self.assertFalse(classify(changed=False, assistant_text=None)["action_only"])


class RestoreReason(unittest.TestCase):
    def test_no_problem_means_no_restore(self):
        self.assertIsNone(sup.restore_reason())

    def test_guard_violation_outranks_later_observations(self):
        # The guard names the earlier, root cause; keep it.
        reason = sup.restore_reason(guard_violation="Task exceeded its 8 minute budget",
                                    document_missing=True, path_changed=True)
        self.assertEqual(reason, "Task exceeded its 8 minute budget")

    def test_closed_document(self):
        self.assertEqual(sup.restore_reason(document_missing=True),
                         "active document was closed")

    def test_path_change_outranks_archive_check(self):
        self.assertEqual(sup.restore_reason(path_changed=True, archive_invalid=True),
                         "document path changed")

    def test_corrupt_archive(self):
        self.assertEqual(sup.restore_reason(archive_invalid=True),
                         "canonical FCStd is missing or corrupt")


class GuardTick(unittest.TestCase):
    def tick(self, **overrides):
        args = {
            "elapsed_seconds": 10.0,
            "budget_seconds": 480,
            "rss_growth_bytes": 0,
            "missing_checks": 0,
        }
        args.update(overrides)
        return sup.evaluate_guard(**args)

    def test_healthy_tick_is_silent(self):
        violation, missing, restore = self.tick()
        self.assertIsNone(violation)
        self.assertEqual(missing, 0)
        self.assertFalse(restore)

    def test_budget_overrun_ends_the_task(self):
        violation, _, _ = self.tick(elapsed_seconds=481, budget_seconds=480)
        self.assertIn("8 minute", violation)

    def test_budget_message_uses_the_tasks_own_budget(self):
        violation, _, _ = self.tick(elapsed_seconds=1501, budget_seconds=1500)
        self.assertIn("25 minute", violation)

    def test_runaway_memory_ends_the_task(self):
        violation, _, _ = self.tick(rss_growth_bytes=2 * 1024 ** 3)
        self.assertIn("1 GiB", violation)

    def test_closed_document_outranks_discovery_error(self):
        # A specific, serious finding replaces the generic transport warning.
        violation, _, _ = self.tick(document_missing=True, discovery_error="socket gone")
        self.assertEqual(violation, "Backend closed the protected FreeCAD document")

    def test_discovery_error_alone_is_reported(self):
        violation, _, _ = self.tick(discovery_error="permission denied")
        self.assertIn("Bridge discovery", violation)

    def test_moved_document(self):
        violation, _, _ = self.tick(path_changed=True)
        self.assertEqual(violation, "Backend changed the protected document path")

    def test_document_violation_outranks_budget(self):
        violation, _, _ = self.tick(document_missing=True, elapsed_seconds=9999)
        self.assertEqual(violation, "Backend closed the protected FreeCAD document")

    def test_single_missing_file_tick_is_tolerated(self):
        # FreeCAD's save is not atomic: the file briefly vanishes while being
        # rewritten, so one miss must not trigger a restore.
        violation, missing, restore = self.tick(canonical_missing=True, missing_checks=0)
        self.assertIsNone(violation)
        self.assertEqual(missing, 1)
        self.assertFalse(restore)

    def test_third_consecutive_miss_restores_the_file(self):
        violation, missing, restore = self.tick(canonical_missing=True, missing_checks=2)
        self.assertEqual(missing, 3)
        self.assertTrue(restore)
        self.assertIn("removed the protected FCStd", violation)

    def test_counter_resets_once_the_file_is_back(self):
        _, missing, restore = self.tick(canonical_missing=False, missing_checks=2)
        self.assertEqual(missing, 0)
        self.assertFalse(restore)



class MutationGuardTests(unittest.TestCase):
    """The runaway guard must not kill work that is merely detailed.

    This guard has produced two false positives against real builds, both found
    the expensive way -- a user watching several minutes of correct modelling get
    rolled back. First a spreadsheet-driven wall hook died at 180 events on its
    parameter sheet; the fix special-cased spreadsheets. Then a towel hook died
    at 180 events on its profile sketch, which is the same situation wearing a
    different type name.

    The lesson these tests encode: a per-object event count measures how detailed
    an object is, not whether the agent is stuck. Sketches and spreadsheets are
    BUILT one event at a time -- a line, a constraint, a cell -- so hundreds of
    events are normal. The total cap is the guard that actually works.
    """

    def test_a_detailed_sketch_is_not_a_runaway(self):
        # The build that was killed: >180 events on one profile sketch.
        self.assertIsNone(sup.mutation_violation(
            626, "ProfileSketch", 400, "Sketcher::SketchObject"))

    def test_a_parameter_spreadsheet_is_not_a_runaway(self):
        self.assertIsNone(sup.mutation_violation(
            900, "Parameters", 500, "Spreadsheet::Sheet"))

    def test_a_genuinely_stuck_sketch_still_stops(self):
        violation = sup.mutation_violation(
            2000, "ProfileSketch", 1501, "Sketcher::SketchObject")
        self.assertIn("ProfileSketch", violation)

    def test_ordinary_geometry_keeps_a_tighter_cap(self):
        self.assertIsNone(sup.mutation_violation(
            700, "MainPad", 600, "PartDesign::Pad"))
        self.assertIsNotNone(sup.mutation_violation(
            700, "MainPad", 601, "PartDesign::Pad"))

    def test_the_total_cap_is_the_real_backstop(self):
        # Whatever the per-object numbers say, an unbounded loop is caught.
        violation = sup.mutation_violation(
            3001, "Anything", 1, "Sketcher::SketchObject")
        self.assertIn("3000", violation)

    def test_the_total_cap_outranks_the_per_object_reason(self):
        violation = sup.mutation_violation(
            5000, "ProfileSketch", 5000, "PartDesign::Pad")
        self.assertIn("mutation events", violation)

    def test_an_unknown_type_gets_the_default_cap(self):
        # Type lookup can fail (object already deleted); that must not silently
        # promote an object to the permissive cap.
        self.assertEqual(sup.object_event_cap(""),
                         sup.DEFAULT_OBJECT_CAP)
        self.assertEqual(sup.object_event_cap(None),
                         sup.DEFAULT_OBJECT_CAP)

    def test_nameless_events_cannot_trigger_the_per_object_rule(self):
        self.assertIsNone(sup.mutation_violation(
            10, "", 9999, "PartDesign::Pad"))

    def test_caps_are_ordered_sensibly(self):
        self.assertGreater(sup.INCREMENTAL_OBJECT_CAP,
                           sup.DEFAULT_OBJECT_CAP)
        self.assertGreater(sup.TOTAL_MUTATION_CAP,
                           sup.INCREMENTAL_OBJECT_CAP)



if __name__ == "__main__":
    unittest.main()
