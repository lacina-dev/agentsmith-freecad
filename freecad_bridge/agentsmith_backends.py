"""Backend CLI adapters: command-line assembly and output-stream parsing.

Extracted from BridgeGui so it can be unit-tested without FreeCAD or Qt. This
module MUST NOT import FreeCAD, FreeCADGui, Part or PySide.

Three CLIs are supported and each speaks its own JSON event dialect
(codex ``item.completed``, claude ``assistant`` / ``result``, copilot
``assistant.message``). That makes this the single most likely place for a
silent regression when any of them changes its output format, which is exactly
why it lives behind tests. ``tests/fixtures/`` holds real captured lines.

Module name is prefixed because every FreeCAD Mod directory shares sys.path.
"""

import json

# Program names as invoked on PATH. The panel overrides these from its own
# backend detection; the eval runner uses them directly.
DEFAULT_BACKEND_COMMANDS = {"codex": "codex", "claude": "claude", "copilot": "copilot"}

# How many reference/render images are attached to one backend call. Beyond a
# handful the context cost outweighs the extra evidence.
MAX_ATTACHED_VISUALS = 5

# Name the bridge's MCP server is registered under. Kept short because the model
# sees it as a tool-name prefix in some clients.
MCP_SERVER_NAME = "agentsmith"


def mcp_config_document(command, args, name=MCP_SERVER_NAME):
    """The `--mcp-config` document claude's CLI expects: {"mcpServers": {...}}.

    Written per task into the project directory rather than registered globally,
    so a modeling run cannot leave a stale server behind in the user's config —
    and so two FreeCAD instances never fight over one registration.
    """
    return {"mcpServers": {name: {"command": command, "args": list(args)}}}


def _mcp_launch_flags(backend, mcp):
    """CLI flags that point a backend at our MCP server for this run only.

    Each CLI takes a different route: claude reads a JSON config file, codex takes
    dotted TOML overrides. Copilot has no documented per-invocation MCP flag, so it
    silently keeps using the bridge CLI client — that is a capability gap, not an
    error, and the caller decides whether to care.
    """
    if not mcp:
        return []
    name = mcp.get("name", MCP_SERVER_NAME)
    if backend == "claude":
        config_path = mcp.get("config_path")
        if not config_path:
            return []
        # --strict-mcp-config keeps the user's own servers out of a modeling run:
        # the worker should see the FreeCAD tools and nothing else.
        return ["--mcp-config", config_path, "--strict-mcp-config"]
    if backend == "codex":
        command = mcp.get("command")
        if not command:
            return []
        return ["-c", 'mcp_servers.%s.command="%s"' % (name, command),
                "-c", "mcp_servers.%s.args=%s" % (name, json.dumps(list(mcp.get("args", []))))]
    return []


def build_launch_args(backend, model, project, context, visual_paths=None,
                      attach_images=True, mcp=None):
    """Build the argument vector for one backend CLI run.

    ``visual_paths`` are absolute image paths (renders, references). Codex and
    copilot take them as real attachment flags; the claude CLI has no image
    flag, so they are surfaced as a header block instructing it to open each
    file with its own image-capable Read tool.

    ``attach_images=False`` skips image handling entirely (the eval sandbox
    runs without visual context).

    ``mcp`` optionally points the backend at the bridge's MCP server for this run:
    ``{"name", "command", "args", "config_path"}``. When omitted the worker drives
    the model through the bridge CLI client exactly as before — the two paths are
    deliberately interchangeable so one can be measured against the other.

    Returns the argument list; the caller supplies the program name.
    Raises ValueError for an unknown backend.
    """
    attached = list(visual_paths or [])[:MAX_ATTACHED_VISUALS] if attach_images else []
    mcp_flags = _mcp_launch_flags(backend, mcp)

    if backend == "codex":
        args = (["exec"] + mcp_flags
                + ["--json", "--skip-git-repo-check", "-C", project,
                   "-s", "danger-full-access", context])
        for path in attached:
            args.extend(["--image", path])
        if model:
            args[1:1] = ["-m", model]
        return args

    if backend == "claude":
        claude_context = context
        if attached:
            header = ("ATTACHED IMAGES (you cannot see them inline — READ each file with "
                      "your image-capable Read tool BEFORE modeling):\n")
            header += "".join("- %s\n" % path for path in attached)
            claude_context = header + "\n" + context
        args = (["-p"] + mcp_flags
                + ["--output-format", "stream-json", "--verbose",
                   "--dangerously-skip-permissions", "--no-session-persistence",
                   claude_context])
        if model:
            args[1:1] = ["--model", model]
        return args

    if backend == "copilot":
        args = ["-C", project, "-p", context, "--output-format", "json",
                "--allow-all", "--no-ask-user", "--no-color", "--no-auto-update"]
        for path in attached:
            args.extend(["--attachment", path])
        if model:
            args[2:2] = ["--model", model]
        return args

    raise ValueError("Unknown backend: %s" % backend)


def backend_event_text(event, backend):
    """Extract the human-readable message(s) from one parsed backend JSON event.

    Returns a list of strings (usually empty or one item). Unknown event types
    and unknown backends yield nothing rather than raising: the stream carries
    plenty of events the panel has no interest in.
    """
    event_type = str(event.get("type", ""))
    texts = []

    if backend == "codex":
        if event_type == "item.completed":
            item = event.get("item", {})
            value = item.get("text") or item.get("content") or item.get("command")
            if value:
                texts.append(str(value))
        elif event_type in ("turn.failed", "error"):
            texts.append("ERROR: " + str(event))

    elif backend == "claude":
        if event_type == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    texts.append(block["text"])
        elif event_type == "result" and event.get("is_error"):
            texts.append("ERROR: " + str(event.get("result") or event))

    elif backend == "copilot":
        if event_type == "assistant.message":
            candidate = (event.get("data", {}).get("content") or event.get("message")
                         or event.get("text") or event.get("content"))
            if isinstance(candidate, str):
                texts.append(candidate)
            elif isinstance(candidate, dict):
                value = candidate.get("content") or candidate.get("text")
                if isinstance(value, str):
                    texts.append(value)
        if "error" in event_type.lower():
            texts.append("ERROR: " + str(event))

    return texts
