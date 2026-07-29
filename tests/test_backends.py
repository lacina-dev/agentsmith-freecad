"""Tests for backend CLI assembly and event parsing (agentsmith_backends).

The event-parsing tests run against fixtures/*.jsonl — REAL lines captured from
live codex and claude runs (tests/fixtures/). If a CLI changes its output format,
refresh the fixture from a fresh .agentsmith/task-history.jsonl and these tests
will show exactly what broke.
"""

import unittest

import helpers

import agentsmith_backends as backends


class LaunchArgs(unittest.TestCase):
    def test_codex_basic_shape(self):
        args = backends.build_launch_args("codex", "", "/proj", "CONTEXT")
        self.assertEqual(args[0], "exec")
        self.assertIn("--skip-git-repo-check", args)
        self.assertEqual(args[args.index("-C") + 1], "/proj")
        self.assertEqual(args[args.index("-s") + 1], "danger-full-access")
        self.assertEqual(args[-1], "CONTEXT")

    def test_codex_model_goes_before_the_context(self):
        args = backends.build_launch_args("codex", "gpt-5", "/proj", "CONTEXT")
        self.assertEqual(args[:3], ["exec", "-m", "gpt-5"])
        self.assertEqual(args[-1], "CONTEXT")

    def test_codex_images_use_the_image_flag(self):
        args = backends.build_launch_args("codex", "", "/proj", "CONTEXT",
                                          ["/tmp/a.png", "/tmp/b.png"])
        self.assertEqual(args.count("--image"), 2)
        self.assertIn("/tmp/b.png", args)

    def test_claude_has_no_image_flag_so_paths_go_into_the_prompt(self):
        args = backends.build_launch_args("claude", "", "/proj", "CONTEXT", ["/tmp/a.png"])
        self.assertNotIn("--image", args)
        prompt = args[-1]
        self.assertIn("/tmp/a.png", prompt)
        self.assertIn("READ each file", prompt)
        self.assertTrue(prompt.endswith("CONTEXT"))

    def test_claude_without_images_sends_the_bare_context(self):
        args = backends.build_launch_args("claude", "", "/proj", "CONTEXT")
        self.assertEqual(args[-1], "CONTEXT")

    def test_claude_flags(self):
        args = backends.build_launch_args("claude", "opus", "/proj", "CONTEXT")
        self.assertEqual(args[:3], ["-p", "--model", "opus"])
        for flag in ("--output-format", "stream-json", "--dangerously-skip-permissions",
                     "--no-session-persistence"):
            self.assertIn(flag, args)

    def test_copilot_shape_and_attachments(self):
        args = backends.build_launch_args("copilot", "gpt-5", "/proj", "CONTEXT", ["/tmp/a.png"])
        self.assertEqual(args[:4], ["-C", "/proj", "--model", "gpt-5"])
        self.assertEqual(args[args.index("-p") + 1], "CONTEXT")
        self.assertEqual(args[args.index("--attachment") + 1], "/tmp/a.png")
        self.assertIn("--allow-all", args)

    def test_attachments_are_capped(self):
        paths = ["/tmp/%d.png" % i for i in range(12)]
        args = backends.build_launch_args("codex", "", "/proj", "CONTEXT", paths)
        self.assertEqual(args.count("--image"), backends.MAX_ATTACHED_VISUALS)

    def test_attach_images_false_drops_them_entirely(self):
        # The eval sandbox runs without visual context.
        args = backends.build_launch_args("claude", "", "/proj", "CONTEXT",
                                          ["/tmp/a.png"], attach_images=False)
        self.assertEqual(args[-1], "CONTEXT")

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            backends.build_launch_args("gemini", "", "/proj", "CONTEXT")


class CodexEvents(unittest.TestCase):
    def setUp(self):
        self.events = {str(e.get("type")): e
                       for e in helpers.load_fixture_events("codex_events.jsonl")}

    def test_fixture_covers_the_expected_types(self):
        self.assertIn("item.completed", self.events)
        self.assertIn("turn.completed", self.events)

    def test_agent_message_is_surfaced(self):
        texts = backends.backend_event_text(self.events["item.completed"], "codex")
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].strip())

    def test_usage_events_are_not_chat(self):
        self.assertEqual(backends.backend_event_text(self.events["turn.completed"], "codex"), [])

    def test_turn_failed_is_reported_as_error(self):
        texts = backends.backend_event_text({"type": "turn.failed", "error": "boom"}, "codex")
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith("ERROR:"))

    def test_command_is_shown_when_there_is_no_text(self):
        event = {"type": "item.completed",
                 "item": {"type": "command_execution", "command": "python3 x.py"}}
        self.assertEqual(backends.backend_event_text(event, "codex"), ["python3 x.py"])


class ClaudeEvents(unittest.TestCase):
    def setUp(self):
        self.events = {str(e.get("type")): e
                       for e in helpers.load_fixture_events("claude_events.jsonl")}

    def test_tool_use_only_message_yields_no_chat_text(self):
        # The captured assistant event carries a tool_use block and no prose.
        texts = backends.backend_event_text(self.events["assistant"], "claude")
        self.assertEqual(texts, [])

    def test_text_blocks_are_surfaced(self):
        event = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Hotovo."},
            {"type": "tool_use", "name": "Bash"},
            {"type": "text", "text": "Uloženo."},
        ]}}
        self.assertEqual(backends.backend_event_text(event, "claude"), ["Hotovo.", "Uloženo."])

    def test_system_and_user_events_are_ignored(self):
        for key in ("system", "user"):
            if key in self.events:
                self.assertEqual(backends.backend_event_text(self.events[key], "claude"), [])

    def test_successful_result_is_not_an_error(self):
        self.assertEqual(
            backends.backend_event_text({"type": "result", "is_error": False}, "claude"), [])

    def test_failed_result_is_reported(self):
        texts = backends.backend_event_text(
            {"type": "result", "is_error": True, "result": "rate limited"}, "claude")
        self.assertEqual(texts, ["ERROR: rate limited"])


class CopilotEvents(unittest.TestCase):
    def test_string_content(self):
        event = {"type": "assistant.message", "data": {"content": "Hotovo."}}
        self.assertEqual(backends.backend_event_text(event, "copilot"), ["Hotovo."])

    def test_nested_dict_content(self):
        event = {"type": "assistant.message", "data": {}, "message": {"text": "Hotovo."}}
        self.assertEqual(backends.backend_event_text(event, "copilot"), ["Hotovo."])

    def test_error_type_is_reported(self):
        texts = backends.backend_event_text({"type": "tool.error", "detail": "x"}, "copilot")
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith("ERROR:"))


class UnknownInput(unittest.TestCase):
    def test_unknown_backend_yields_nothing(self):
        self.assertEqual(backends.backend_event_text({"type": "assistant"}, "gemini"), [])

    def test_event_without_type_yields_nothing(self):
        self.assertEqual(backends.backend_event_text({}, "codex"), [])



class McpPython(unittest.TestCase):
    """The MCP server interpreter must be a python, never the host binary.

    Found live (2026-07-26): inside the AppImage, sys.executable is the freecad
    binary, so the panel wrote `/tmp/.mount_*/usr/bin/freecad mcp_server.py`
    into mcp.json. The backend then "started the MCP server" by booting a whole
    second FreeCAD GUI — and after a restart the dead mount path made every
    MCP connect fail.
    """

    def test_freecad_appimage_executable_is_rejected(self):
        found = backends.mcp_python("/tmp/.mount_freecaXYZ/usr/bin/freecad",
                                    which=lambda name: "/usr/bin/python3")
        self.assertEqual(found, "/usr/bin/python3")

    def test_real_python_is_kept(self):
        self.assertEqual(backends.mcp_python("/usr/bin/python3.12"), "/usr/bin/python3.12")

    def test_no_python_found_still_names_one_for_path_resolution(self):
        # The backend CLI resolves the command in the user's shell PATH, which
        # is healthier than the AppImage's environment — a bare name is the
        # right last resort, silence is not.
        self.assertEqual(backends.mcp_python("freecad", which=lambda name: None), "python3")


class McpWiring(unittest.TestCase):
    """Pointing a backend at the bridge's MCP server, per run and per CLI."""

    CLAUDE = {"name": "agentsmith", "command": "python3",
              "args": ["/mod/mcp_server.py"], "config_path": "/proj/.agentsmith/mcp.json"}

    def test_config_document_shape(self):
        document = backends.mcp_config_document("python3", ["/mod/mcp_server.py"])
        self.assertEqual(document["mcpServers"]["agentsmith"],
                         {"command": "python3", "args": ["/mod/mcp_server.py"]})

    def test_claude_gets_a_config_file_and_strict_mode(self):
        args = backends.build_launch_args("claude", "", "/proj", "CTX", mcp=self.CLAUDE)
        self.assertEqual(args[args.index("--mcp-config") + 1], "/proj/.agentsmith/mcp.json")
        # Without strict mode the worker would also see the user's own MCP servers.
        self.assertIn("--strict-mcp-config", args)
        self.assertEqual(args[-1], "CTX")

    def test_codex_gets_dotted_config_overrides(self):
        args = backends.build_launch_args(
            "codex", "", "/proj", "CTX",
            mcp={"command": "python3", "args": ["/mod/mcp_server.py"]})
        self.assertIn('mcp_servers.agentsmith.command="python3"', args)
        self.assertIn('mcp_servers.agentsmith.args=["/mod/mcp_server.py"]', args)
        self.assertEqual(args[0], "exec")
        self.assertEqual(args[-1], "CTX")

    def test_model_flag_still_comes_first_with_mcp_on(self):
        args = backends.build_launch_args("codex", "gpt-5.6-sol", "/proj", "CTX",
                                          mcp={"command": "python3", "args": []})
        self.assertEqual(args[:3], ["exec", "-m", "gpt-5.6-sol"])
        args = backends.build_launch_args("claude", "opus", "/proj", "CTX", mcp=self.CLAUDE)
        self.assertEqual(args[:3], ["-p", "--model", "opus"])

    def test_no_mcp_means_the_old_command_line_exactly(self):
        self.assertEqual(backends.build_launch_args("codex", "m", "/p", "C"),
                         backends.build_launch_args("codex", "m", "/p", "C", mcp=None))

    def test_incomplete_mcp_settings_are_ignored(self):
        # claude needs the config file; codex needs the command. Missing either must
        # degrade to the CLI-client path rather than emit half a flag pair.
        self.assertNotIn("--mcp-config",
                         backends.build_launch_args("claude", "", "/p", "C", mcp={"name": "x"}))
        self.assertNotIn("-c",
                         backends.build_launch_args("codex", "", "/p", "C", mcp={"name": "x"}))

    def test_copilot_has_no_mcp_flag_and_is_unchanged(self):
        with_mcp = backends.build_launch_args("copilot", "", "/p", "C", mcp=self.CLAUDE)
        self.assertEqual(with_mcp, backends.build_launch_args("copilot", "", "/p", "C"))

if __name__ == "__main__":
    unittest.main()
