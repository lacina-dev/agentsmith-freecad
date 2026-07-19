#!/usr/bin/env python3
"""Small command-line client for the FreeCAD AgentSmith bridge."""

import argparse
import json
import socket


DISCOVERY_FILE = "/tmp/freecad-agentsmith-bridge.json"


def call(command, args=None):
    with open(DISCOVERY_FILE, "r", encoding="utf-8") as handle:
        discovery = json.load(handle)
    request = {
        "token": discovery["token"],
        "command": command,
        "args": args or {},
    }
    with socket.create_connection((discovery["host"], discovery["port"]), timeout=10) as connection:
        connection.sendall((json.dumps(request) + "\n").encode("utf-8"))
        response = b""
        while b"\n" not in response:
            block = connection.recv(65536)
            if not block:
                break
            response += block
    return json.loads(response.split(b"\n", 1)[0].decode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("args", nargs="?", default="{}", help="JSON object")
    options = parser.parse_args()
    response = call(options.command, json.loads(options.args))
    print(json.dumps(response, indent=2, ensure_ascii=False))
    # Non-zero exit on a bridge-level error so shell && chains stop instead of
    # silently continuing past a failed step.
    if isinstance(response, dict) and response.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

