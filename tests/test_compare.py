"""Tests for eval/compare.py — the tool that decides whether a change helped.

If this aggregates wrong, every later "did the harness improve?" answer is wrong,
so the pass-rate arithmetic and the regression verdict are pinned here.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

import helpers  # noqa: F401

sys.path.insert(0, os.path.join(helpers.REPO_ROOT, "eval"))
import compare  # noqa: E402


def grade(task_id, checks, all_passed=None):
    """A run_eval-shaped report: checks is {name: status}."""
    results = [{"check": name, "status": status} for name, status in checks.items()]
    graded = [r for r in results if r["status"] != "skipped"]
    passed = [r for r in graded if r["status"] == "pass"]
    return {
        "task_id": task_id,
        "results": results,
        "passed_count": len(passed),
        "graded_count": len(graded),
        "all_passed": bool(graded) and len(passed) == len(graded)
        if all_passed is None else all_passed,
    }


def record(task_id, checks, model="m", **extra):
    """A run_e2e-shaped record wrapping a grade."""
    return {"task_id": task_id, "backend": "claude", "model": model,
            "grade": grade(task_id, checks), **extra}


class Aggregation(unittest.TestCase):
    def test_repeated_runs_become_a_pass_rate(self):
        snapshot = compare.load_snapshot_from_records([
            record("hook", {"validate": "pass"}),
            record("hook", {"validate": "fail"}),
            record("hook", {"validate": "pass"}),
        ])
        rates = compare._check_rates(snapshot["hook"]["runs"])
        self.assertEqual(rates["validate"], (2, 3))

    def test_skipped_runs_count_toward_neither_number(self):
        snapshot = compare.load_snapshot_from_records([
            record("hook", {"print_readiness": "skipped"}),
            record("hook", {"print_readiness": "pass"}),
        ])
        self.assertEqual(compare._check_rates(snapshot["hook"]["runs"])["print_readiness"],
                         (1, 1))

    def test_errors_count_as_not_passing(self):
        snapshot = compare.load_snapshot_from_records([
            record("hook", {"validate": "error"}),
            record("hook", {"validate": "pass"}),
        ])
        self.assertEqual(compare._check_rates(snapshot["hook"]["runs"])["validate"], (1, 2))


class Verdict(unittest.TestCase):
    def diff(self, before_checks, after_checks, repeats=1):
        before = compare.load_snapshot_from_records(
            [record("hook", before_checks) for _ in range(repeats)])
        after = compare.load_snapshot_from_records(
            [record("hook", after_checks) for _ in range(repeats)])
        return compare.compare(before, after)[0]

    def test_same_result_is_not_a_regression(self):
        task = self.diff({"validate": "pass"}, {"validate": "pass"})
        self.assertEqual(task["regressed"], [])
        self.assertEqual(task["checks"][0]["delta"], "same")

    def test_pass_to_fail_is_a_regression(self):
        task = self.diff({"validate": "pass"}, {"validate": "fail"})
        self.assertEqual(task["regressed"], ["validate"])

    def test_fail_to_pass_is_an_improvement(self):
        task = self.diff({"validate": "fail"}, {"validate": "pass"})
        self.assertEqual(task["fixed"], ["validate"])
        self.assertEqual(task["regressed"], [])

    def test_a_new_check_is_not_a_regression(self):
        task = self.diff({"validate": "pass"}, {"validate": "pass", "functional_protrusion": "fail"})
        deltas = {c["check"]: c["delta"] for c in task["checks"]}
        self.assertEqual(deltas["functional_protrusion"], "added")
        self.assertEqual(task["regressed"], [])

    def test_partial_rate_drop_is_a_regression(self):
        before = compare.load_snapshot_from_records([
            record("hook", {"validate": "pass"}), record("hook", {"validate": "pass"})])
        after = compare.load_snapshot_from_records([
            record("hook", {"validate": "pass"}), record("hook", {"validate": "fail"})])
        task = compare.compare(before, after)[0]
        self.assertEqual(task["regressed"], ["validate"])
        self.assertEqual(task["checks"][0]["before"], (2, 2))
        self.assertEqual(task["checks"][0]["after"], (1, 2))


class FileIO(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="compare-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, payload):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def test_reads_run_e2e_and_run_eval_shapes_alike(self):
        e2e = self.write("a.json", [record("hook", {"validate": "pass"})])
        plain = self.write("b.json", [grade("hook", {"validate": "pass"})])
        self.assertIn("hook", compare.load_snapshot(e2e))
        self.assertIn("hook", compare.load_snapshot(plain))

    def test_single_object_snapshot(self):
        path = self.write("c.json", grade("hook", {"validate": "pass"}))
        self.assertIn("hook", compare.load_snapshot(path))

    def test_exit_code_signals_regression(self):
        before = self.write("before.json", [record("hook", {"validate": "pass"})])
        after = self.write("after.json", [record("hook", {"validate": "fail"})])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(compare.main([before, after]), 1)
        self.assertIn("regression", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compare.main([before, before]), 0)

    def test_json_output_is_machine_readable(self):
        before = self.write("before.json", [record("hook", {"validate": "pass"})])
        after = self.write("after.json", [record("hook", {"validate": "fail"})])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            compare.main([before, after, "--json", "--labels", "a,b"])
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["a"], "a")
        self.assertEqual(payload["tasks"][0]["regressed"], ["validate"])

    def test_unreadable_input_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            compare.load_snapshot(os.path.join(self.dir, "missing.json"))
        self.assertEqual(caught.exception.code, 2)

    def test_no_comparable_tasks_returns_2(self):
        empty = self.write("empty.json", [])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(compare.main([empty, empty]), 2)


if __name__ == "__main__":
    unittest.main()


class SnapshotSelectionTests(unittest.TestCase):
    """Picking runs out of the append-only history.

    A comparison is only worth anything if both sides differ in exactly one thing.
    Two runs that differ only in a flag look identical in every other field, so
    selection has to be explicit — quietly mixing an MCP run into a non-MCP
    snapshot would produce a diff that measures nothing and looks convincing.
    """

    @classmethod
    def setUpClass(cls):
        import sys, os
        sys.path.insert(0, os.path.join(helpers.REPO_ROOT, "eval"))
        import snapshot_from_history
        cls.mod = snapshot_from_history

    def rows(self):
        return [
            {"task_id": "a", "model": "opus", "score": "5/8", "mcp": False,
             "timestamp_iso": "2026-07-25T10:00:00"},
            {"task_id": "a", "model": "opus", "score": "8/8", "mcp": False,
             "timestamp_iso": "2026-07-25T12:00:00"},
            {"task_id": "a", "model": "opus", "score": "7/8", "mcp": True,
             "timestamp_iso": "2026-07-25T14:00:00"},
            {"task_id": "b", "model": "sonnet", "score": "6/6", "mcp": False,
             "timestamp_iso": "2026-07-25T12:00:00"},
            {"task_id": "c", "model": "opus", "score": "4/9", "mcp": False,
             "grade_before_review": {"score": "2/9"},
             "timestamp_iso": "2026-07-25T15:00:00"},
        ]

    def test_the_newest_run_of_a_task_wins(self):
        picked = self.mod.select(self.rows(), tasks=["a"], mcp=False)
        self.assertEqual([r["score"] for r in picked], ["8/8"])

    def test_mcp_runs_are_not_mixed_into_a_plain_snapshot(self):
        picked = self.mod.select(self.rows(), tasks=["a"], mcp=False)
        self.assertTrue(all(not r["mcp"] for r in picked))
        picked = self.mod.select(self.rows(), tasks=["a"], mcp=True)
        self.assertEqual([r["score"] for r in picked], ["7/8"])

    def test_model_filter_separates_backends(self):
        picked = self.mod.select(self.rows(), model="sonnet")
        self.assertEqual([r["task_id"] for r in picked], ["b"])

    def test_review_runs_are_identified_by_having_a_before_score(self):
        picked = self.mod.select(self.rows(), review=True)
        self.assertEqual([r["task_id"] for r in picked], ["c"])
        picked = self.mod.select(self.rows(), review=False)
        self.assertNotIn("c", [r["task_id"] for r in picked])

    def test_since_excludes_older_runs(self):
        picked = self.mod.select(self.rows(), tasks=["a"], mcp=False,
                                 since="2026-07-25T11:00:00")
        self.assertEqual([r["score"] for r in picked], ["8/8"])
        self.assertEqual(self.mod.select(self.rows(), tasks=["a"], mcp=False,
                                         since="2026-07-26T00:00:00"), [])

    def test_no_filters_returns_one_run_per_task(self):
        picked = self.mod.select(self.rows())
        self.assertEqual(sorted(r["task_id"] for r in picked), ["a", "b", "c"])
