#!/usr/bin/env python3
"""mcp_server.py -- expose the AgentSmith FreeCAD bridge as an MCP server.

Speaks JSON-RPC 2.0 over stdio (one message per line) and forwards each tool call
to the bridge socket that the FreeCAD panel is listening on. The protocol logic
lives in agentsmith_mcp.py; this file is transport only.

Usage (normally started by the backend, not by hand):
    python3 mcp_server.py [--discovery PATH]

Register it with a backend, e.g.:
    claude mcp add agentsmith -- python3 /path/to/mcp_server.py
    codex mcp add agentsmith -- python3 /path/to/mcp_server.py

Exit codes:
    0  stdin closed cleanly (the client went away)
    2  the bridge discovery file names a server we cannot reach at start-up

The bridge does NOT have to be running when the server starts: FreeCAD may be
opened later, and a client that spawned us at session start would otherwise hold
a dead server. Connection failures surface per call as tool errors instead.

Stdlib only.
"""

import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentsmith_mcp  # noqa: E402

DEFAULT_DISCOVERY = "/tmp/freecad-agentsmith-bridge.json"
DEFAULT_TIMEOUT = 30.0


class BridgeUnavailable(RuntimeError):
    """The bridge socket could not be reached — reported per tool call."""


def bridge_call(command, args, discovery_file=DEFAULT_DISCOVERY, timeout=DEFAULT_TIMEOUT):
    """One request/response round trip against the bridge socket.

    Raises BridgeUnavailable when FreeCAD/the panel is not there, and RuntimeError
    with the bridge's own message when the command itself failed — the caller turns
    both into a tool error so the model sees the reason.
    """
    try:
        with open(discovery_file, "r", encoding="utf-8") as handle:
            discovery = json.load(handle)
    except FileNotFoundError:
        raise BridgeUnavailable(
            "no bridge is running (discovery file %s not found). Open FreeCAD, select "
            "the AgentSmith workbench and press Start bridge." % discovery_file)
    except (OSError, ValueError) as exc:
        raise BridgeUnavailable("cannot read %s: %s" % (discovery_file, exc))

    for key in ("host", "port", "token"):
        if key not in discovery:
            raise BridgeUnavailable("discovery file %s is missing %r" % (discovery_file, key))

    request = {"token": discovery["token"], "command": command, "args": args or {}}
    try:
        with socket.create_connection((discovery["host"], discovery["port"]),
                                      timeout=timeout) as connection:
            connection.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response = b""
            while b"\n" not in response:
                block = connection.recv(65536)
                if not block:
                    break
                response += block
    except (OSError, socket.timeout) as exc:
        raise BridgeUnavailable("bridge at %s:%s is not answering (%s)"
                                % (discovery.get("host"), discovery.get("port"), exc))

    if not response:
        raise BridgeUnavailable("bridge closed the connection without answering %r" % command)
    try:
        parsed = json.loads(response.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("bridge returned an unparseable response: %s" % exc)

    if not parsed.get("ok"):
        raise RuntimeError(str(parsed.get("error", "unknown bridge error")))
    result = parsed.get("result")
    # The bridge attaches a budget countdown to every response while a task
    # runs. The CLI wrapper prints the whole envelope; here only "result" is
    # shown to the model, so carry the clock across.
    if isinstance(result, dict) and "budget" in parsed:
        result = dict(result)
        result["budget"] = parsed["budget"]
    return result


def serve(stdin, stdout, call_bridge, server_version="0.0.0"):
    """Read line-delimited JSON-RPC from stdin, write responses to stdout.

    A malformed line is answered with a parse error rather than killing the
    server: one bad message must not take down a session.
    """
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError as exc:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": agentsmith_mcp.PARSE_ERROR,
                                  "message": "invalid JSON: %s" % exc}}
        else:
            response = agentsmith_mcp.handle_request(request, call_bridge, server_version)
        if response is None:
            continue  # notification
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()


def _server_version(discovery_file):
    """Report the bridge's version when it is up, so a client's serverInfo is
    truthful; fall back to the addon's own constant when it is not."""
    try:
        return str(bridge_call("ping", {}, discovery_file, timeout=3).get("bridge_version"))
    except Exception:
        return "unknown"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--discovery", default=DEFAULT_DISCOVERY,
                        help="Bridge discovery file (default %s)" % DEFAULT_DISCOVERY)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="Per-call socket timeout in seconds")
    args = parser.parse_args(argv)

    def call(command, arguments):
        return bridge_call(command, arguments, args.discovery, args.timeout)

    serve(sys.stdin, sys.stdout, call, _server_version(args.discovery))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
