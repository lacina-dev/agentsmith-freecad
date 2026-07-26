"""Tests for the MCP surface (agentsmith_mcp + mcp_server transport).

The point of MCP here is that a wrong tool name or a wrong argument fails at the
client instead of halfway through a model, so the tests focus on: does the
handshake work, is every advertised tool real, and does a failing bridge produce
a readable tool error rather than a dead session.
"""

import io
import json
import os
import sys
import unittest

import helpers  # noqa: F401

import agentsmith_mcp as mcp  # noqa: E402

sys.path.insert(0, helpers.ADDON_DIR)
import mcp_server  # noqa: E402


def request(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


class Recorder:
    """Stands in for the bridge: records calls, returns or raises what you set."""

    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}
        self.error = error

    def __call__(self, command, args):
        self.calls.append((command, args))
        if self.error is not None:
            raise self.error
        return self.result


class Handshake(unittest.TestCase):
    def test_initialize_reports_tools_capability(self):
        response = mcp.handle_request(
            request("initialize", {"protocolVersion": mcp.PROTOCOL_VERSION}), Recorder())
        result = response["result"]
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], mcp.SERVER_NAME)
        self.assertIn("model_digest", result["instructions"])

    def test_known_older_protocol_is_echoed_back(self):
        # Answering with the client's own revision keeps older clients working.
        response = mcp.handle_request(
            request("initialize", {"protocolVersion": "2024-11-05"}), Recorder())
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")

    def test_unknown_protocol_falls_back_to_ours(self):
        response = mcp.handle_request(
            request("initialize", {"protocolVersion": "1999-01-01"}), Recorder())
        self.assertEqual(response["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)

    def test_notifications_get_no_reply(self):
        self.assertIsNone(mcp.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, Recorder()))

    def test_non_jsonrpc_message_is_rejected(self):
        response = mcp.handle_request({"method": "tools/list"}, Recorder())
        self.assertEqual(response["error"]["code"], mcp.INVALID_REQUEST)

    def test_unsupported_method(self):
        response = mcp.handle_request(request("resources/list"), Recorder())
        self.assertEqual(response["error"]["code"], mcp.METHOD_NOT_FOUND)


class Catalogue(unittest.TestCase):
    def setUp(self):
        self.tools = mcp.handle_request(request("tools/list"), Recorder())["result"]["tools"]

    def test_every_tool_has_a_description_and_schema(self):
        for tool in self.tools:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["description"].strip())
                self.assertEqual(tool["inputSchema"]["type"], "object")
                # Closed schemas are the whole point: a typo in an argument name
                # must be rejected by the client, not silently ignored.
                self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_read_only_tools_are_annotated(self):
        annotations = {t["name"]: t["annotations"]["readOnlyHint"] for t in self.tools}
        self.assertTrue(annotations["model_digest"])
        self.assertTrue(annotations["feature_probe"])
        self.assertFalse(annotations["spreadsheet_set"])
        self.assertFalse(annotations["execute_python"])

    def test_catalogue_matches_the_bridge_dispatch_table(self):
        """Every advertised tool must be a command the bridge actually implements."""
        source = helpers.gui_source()
        start = source.index("handlers = {")
        block = source[start:source.index("}", start)]
        implemented = set(part.split('"')[1] for part in block.split("\n")
                          if part.strip().startswith('"'))
        advertised = {tool["name"] for tool in self.tools}
        self.assertEqual(advertised - implemented, set(),
                         "advertised but not implemented by the bridge")

    def test_read_only_flags_match_the_bridge(self):
        source = helpers.gui_source()
        line = next(l for l in source.split("\n") if "read_commands = {" in l)
        bridge_read_only = set(part.split('"')[1] for part in line.split(", ") if '"' in part)
        for tool in self.tools:
            if tool["name"] in bridge_read_only:
                with self.subTest(tool=tool["name"]):
                    self.assertTrue(tool["annotations"]["readOnlyHint"],
                                    "%s is read-only in the bridge" % tool["name"])


class ToolCalls(unittest.TestCase):
    def test_arguments_reach_the_bridge_unchanged(self):
        recorder = Recorder(result={"objects": []})
        mcp.handle_request(request("tools/call", {
            "name": "feature_probe", "arguments": {"min_plane_area_mm2": 5}}), recorder)
        self.assertEqual(recorder.calls, [("feature_probe", {"min_plane_area_mm2": 5})])

    def test_result_is_returned_as_readable_json(self):
        response = mcp.handle_request(request("tools/call", {
            "name": "ping", "arguments": {}}), Recorder(result={"bridge_version": "0.18.1"}))
        payload = response["result"]
        self.assertFalse(payload["isError"])
        self.assertIn("0.18.1", payload["content"][0]["text"])

    def test_unknown_tool_is_a_protocol_error(self):
        response = mcp.handle_request(request("tools/call", {
            "name": "make_me_a_sandwich", "arguments": {}}), Recorder())
        self.assertEqual(response["error"]["code"], mcp.INVALID_PARAMS)

    def test_a_failing_bridge_is_a_tool_error_not_a_dead_session(self):
        # The model must be able to read why and try something else.
        response = mcp.handle_request(request("tools/call", {
            "name": "save", "arguments": {}}), Recorder(error=RuntimeError("read-only mode")))
        payload = response["result"]
        self.assertTrue(payload["isError"])
        self.assertIn("read-only mode", payload["content"][0]["text"])

    def test_non_object_arguments_are_rejected(self):
        response = mcp.handle_request(request("tools/call", {
            "name": "ping", "arguments": ["nope"]}), Recorder())
        self.assertEqual(response["error"]["code"], mcp.INVALID_PARAMS)


class StdioTransport(unittest.TestCase):
    def serve(self, lines, call_bridge=None):
        stdin = io.StringIO("\n".join(json.dumps(l) if isinstance(l, dict) else l
                                      for l in lines) + "\n")
        stdout = io.StringIO()
        mcp_server.serve(stdin, stdout, call_bridge or Recorder(), "0.18.1")
        return [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]

    def test_full_session(self):
        responses = self.serve([
            request("initialize", {"protocolVersion": mcp.PROTOCOL_VERSION}, 1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            request("tools/list", None, 2),
            request("tools/call", {"name": "ping", "arguments": {}}, 3),
        ])
        self.assertEqual([r["id"] for r in responses], [1, 2, 3])
        self.assertEqual(responses[0]["result"]["serverInfo"]["version"], "0.18.1")

    def test_malformed_line_does_not_kill_the_server(self):
        responses = self.serve(["{not json", request("tools/list", None, 7)])
        self.assertEqual(responses[0]["error"]["code"], mcp.PARSE_ERROR)
        self.assertEqual(responses[1]["id"], 7)

    def test_blank_lines_are_ignored(self):
        self.assertEqual(len(self.serve(["", "  ", request("tools/list", None, 1)])), 1)

    def test_missing_bridge_produces_a_helpful_tool_error(self):
        def call(command, args):
            return mcp_server.bridge_call(command, args, "/nonexistent/discovery.json")
        responses = self.serve(
            [request("tools/call", {"name": "ping", "arguments": {}}, 1)], call)
        text = responses[0]["result"]["content"][0]["text"]
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertIn("Start bridge", text)


if __name__ == "__main__":
    unittest.main()
