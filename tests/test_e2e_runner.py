"""Tests for the parts of run_e2e.py that decide what gets measured.

The eval runner spends real money every time it runs, so the pieces that shape a
run — which flags reach the backend, whether the review loop fires, what ends up
in the record — are worth pinning without spending any.

Both features under test exist because a claim had no evidence behind it. The MCP
server and the N-round review loop were built, tested as units and shipped, and
then could not be compared against anything: run_e2e drove the backend directly
and knew about neither. A capability nobody can measure is a capability nobody
should trust, which is what these flags fix.
"""

import json
import os
import sys
import tempfile
import unittest

import helpers  # noqa: F401

sys.path.insert(0, os.path.join(helpers.REPO_ROOT, "eval"))
sys.path.insert(0, helpers.ADDON_DIR)
import agentsmith_backends  # noqa: E402


class Args(object):
    """Just enough of argparse's namespace for the functions under test."""

    def __init__(self, **kwargs):
        self.backend = "claude"
        self.model = "claude-opus-5"
        self.reviewer_model = None
        self.task_timeout = 900
        self.discovery = "/tmp/discovery.json"
        self.mcp = False
        self.review = False
        self.workdir = "/tmp/workdir"
        self.__dict__.update(kwargs)


class McpWiringTests(unittest.TestCase):
    """--mcp must reach the backend command line, or the flag measures nothing."""

    def test_claude_gets_a_config_file_and_strict_flag(self):
        mcp = {"name": "agentsmith", "command": "python3", "args": ["/x/mcp_server.py"],
               "config_path": "/proj/.agentsmith/mcp.json"}
        args = agentsmith_backends.build_launch_args(
            "claude", "claude-opus-5", "/proj", "ctx", attach_images=False, mcp=mcp)
        self.assertIn("--mcp-config", args)
        self.assertIn("/proj/.agentsmith/mcp.json", args)
        self.assertIn("--strict-mcp-config", args)

    def test_without_mcp_nothing_is_added(self):
        args = agentsmith_backends.build_launch_args(
            "claude", "claude-opus-5", "/proj", "ctx", attach_images=False, mcp=None)
        self.assertNotIn("--mcp-config", args)

    def test_the_config_document_points_at_the_server(self):
        document = agentsmith_backends.mcp_config_document("python3", ["/x/mcp_server.py"])
        server = document["mcpServers"][agentsmith_backends.MCP_SERVER_NAME]
        self.assertEqual(server["command"], "python3")
        self.assertEqual(server["args"], ["/x/mcp_server.py"])

    def test_config_is_written_into_the_sandbox_not_the_user_config(self):
        # A measurement run must not leave a server registered behind it.
        import run_e2e
        with tempfile.TemporaryDirectory() as project:
            settings = run_e2e.mcp_settings(project)
            self.assertIsNotNone(settings)
            self.assertTrue(settings["config_path"].startswith(project))
            with open(settings["config_path"], encoding="utf-8") as handle:
                self.assertIn("mcpServers", json.load(handle))


class ReviewLoopTests(unittest.TestCase):
    """The loop must only run when it can change something, and record both scores."""

    def setUp(self):
        import run_e2e
        self.run_e2e = run_e2e
        self.launched = []
        self.real_launch = run_e2e.launch_backend

    def tearDown(self):
        self.run_e2e.launch_backend = self.real_launch

    def fake_launch(self, review_text, review_exit=0):
        def launch(backend, model, context, log_path, timeout, project, mcp=None):
            self.launched.append({"model": model, "context": context, "log": log_path})
            if log_path.endswith(".review.log"):
                with open(log_path, "w", encoding="utf-8") as handle:
                    handle.write(review_text)
                return review_exit, False, 1.0
            return 0, False, 1.0
        self.run_e2e.launch_backend = launch

    def task(self):
        return {"id": "t", "title": "T", "prompt": "Model a bracket.", "checks": {}}

    def review(self, text, exit_code=0, **kwargs):
        self.fake_launch(text, exit_code)
        with tempfile.TemporaryDirectory() as project:
            return self.run_e2e.run_review_round(
                Args(**kwargs), self.task(), "doc",
                os.path.join(project, "t.log"), project, None)

    def test_findings_trigger_a_corrective_round(self):
        findings, fix_exit = self.review(
            "some chatter\nFINDING: wall is 2 mm, request said 3 mm\nmore chatter\n")
        self.assertIn("wall is 2 mm", findings)
        self.assertEqual(fix_exit, 0)
        self.assertEqual(len(self.launched), 2, "reviewer, then one corrective round")
        self.assertIn("CORRECTIVE ROUND", self.launched[1]["context"])
        self.assertIn("wall is 2 mm", self.launched[1]["context"])

    def test_a_clean_review_costs_nothing_more(self):
        findings, fix_exit = self.review("NO FINDINGS\n")
        self.assertIsNone(fix_exit)
        self.assertEqual(len(self.launched), 1, "no corrective round without findings")

    def test_silence_is_not_treated_as_approval_or_as_a_defect(self):
        findings, fix_exit = self.review("")
        self.assertIsNone(findings)
        self.assertIsNone(fix_exit)
        self.assertEqual(len(self.launched), 1)

    def test_a_failed_reviewer_does_not_start_a_corrective_round(self):
        # Fixing a model on the strength of a crashed reviewer's partial output is
        # worse than not reviewing at all.
        findings, fix_exit = self.review("FINDING: something\n", exit_code=1)
        self.assertIsNone(fix_exit)
        self.assertEqual(len(self.launched), 1)

    def test_only_finding_lines_are_passed_on(self):
        findings, _ = self.review(
            "I will now inspect the model.\nFINDING: hole is 5.5 mm\n"
            "Overall this looks quite good.\nFINDING: no chamfer\n")
        self.assertEqual(findings.splitlines(),
                         ["FINDING: hole is 5.5 mm", "FINDING: no chamfer"])

    def test_the_reviewer_can_use_a_different_model(self):
        self.review("NO FINDINGS", reviewer_model="claude-haiku-4-5")
        self.assertEqual(self.launched[0]["model"], "claude-haiku-4-5")

    def test_the_reviewer_is_told_not_to_mutate(self):
        self.review("NO FINDINGS")
        context = self.launched[0]["context"]
        self.assertIn("inspect only", context.lower())
        self.assertIn("do not modify", context.lower())



class FindingExtractionTests(unittest.TestCase):
    """Reading the reviewer's log, which is a JSON event stream and not prose.

    The first version of this scanned for lines starting with "FINDING:" and found
    nothing ever — the reviewer's text lives inside JSON strings where a newline is
    two characters, so the whole report is one physical line. On the first real run
    that silently discarded a correct report of a 40 mm arm that should have been
    60 mm, and made the review loop look worthless when it was the reader that was
    broken.
    """

    def setUp(self):
        import run_e2e
        self.run_e2e = run_e2e
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            os.unlink(path)

    def log(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False,
                                             encoding="utf-8")
        handle.write(text)
        handle.close()
        self.paths.append(handle.name)
        return handle.name

    def test_findings_inside_a_json_stream_are_found(self):
        line = json.dumps({"type": "result", "result":
                           "Inspection complete.\nFINDING: arm is 40 mm, not 60 mm\n"})
        found = self.run_e2e.extract_findings(self.log(line + "\n"), "claude")
        self.assertEqual(found, "FINDING: arm is 40 mm, not 60 mm")

    def test_plain_text_logs_still_work(self):
        found = self.run_e2e.extract_findings(
            self.log("chatter\nFINDING: hole is 5.5 mm\n"), "claude")
        self.assertEqual(found, "FINDING: hole is 5.5 mm")

    def test_a_clean_review_yields_nothing(self):
        line = json.dumps({"type": "result", "result": "All checks pass.\nNO FINDINGS"})
        self.assertEqual(self.run_e2e.extract_findings(self.log(line), "claude"), "")

    def test_markdown_bullets_do_not_hide_a_finding(self):
        line = json.dumps({"type": "result", "result": "- FINDING: wall too thin\n"})
        self.assertIn("wall too thin",
                      self.run_e2e.extract_findings(self.log(line), "claude"))

    def test_duplicates_are_reported_once(self):
        line = json.dumps({"type": "result",
                           "result": "FINDING: same thing\nFINDING: same thing\n"})
        found = self.run_e2e.extract_findings(self.log(line), "claude")
        self.assertEqual(found.count("same thing"), 1)

    def test_a_missing_log_is_not_an_error(self):
        self.assertEqual(self.run_e2e.extract_findings("/nonexistent.log", "claude"), "")

    def test_malformed_lines_do_not_stop_the_scan(self):
        text = "{not json\n" + json.dumps({"type": "result", "result": "FINDING: real one"})
        self.assertIn("real one", self.run_e2e.extract_findings(self.log(text), "claude"))

if __name__ == "__main__":
    unittest.main()
