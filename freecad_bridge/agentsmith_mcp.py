"""MCP (Model Context Protocol) surface for the AgentSmith FreeCAD bridge.

Why this exists: the worker currently drives the model by shelling out to
`python3 freecad_bridge_client.py <command> '<json>'` and parsing stdout. That
works, but every call pays for shell quoting, a Python start-up, and a round of
"what were the arguments again?" — and nothing stops the model from inventing a
command that does not exist. Exposing the same bridge as MCP tools gives the
backend typed schemas it can see, so wrong argument names fail at the client
instead of halfway through a model.

This module is the PROTOCOL half and imports no FreeCAD, no Qt and no sockets:
`handle_request` takes a parsed JSON-RPC request plus a `call_bridge(command,
args)` callable and returns the response dict (or None for notifications).
`mcp_server.py` supplies the stdio loop and the socket transport. Splitting it
this way is what makes the whole surface testable without a running FreeCAD.
"""

import json

# Protocol revision this server speaks. When a client asks for a different one we
# echo its version back if we can work with it (the wire shape has been stable
# across these revisions) and otherwise answer with ours — the spec expects the
# server to state what it actually supports rather than fail the handshake.
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

SERVER_NAME = "agentsmith-freecad"

# JSON-RPC error codes (the subset the spec assigns meaning to).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_DOCUMENT = {"document": {"type": "string",
                          "description": "Document name; omit for the active document."}}


def _schema(properties=None, required=None, with_document=True):
    props = dict(_DOCUMENT) if with_document else {}
    props.update(properties or {})
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_BOOL = {"type": "boolean"}
_OBJECT_NAMES = {"type": "array", "items": {"type": "string"},
                 "description": "Object names or labels; omit for all visible geometry."}

# The tool catalogue. `read_only` mirrors the bridge's own classification: those
# commands stay available when the panel forces read-only mode (the reviewer pass),
# and the rest are rejected there by the bridge itself — we surface that as a tool
# annotation so a client can grey them out rather than discover it by failing.
TOOLS = [
    ("ping", "Check the bridge is alive; returns bridge/FreeCAD version and access level.",
     _schema(with_document=False), True),
    ("capabilities", "List the commands and features this bridge build supports.",
     _schema(with_document=False), True),
    ("documents", "List open documents and which one is active.",
     _schema(with_document=False), True),
    ("document_info", "Name, file path, object count and modified flag of a document.",
     _schema(), True),
    ("object_info", "Properties, placement and shape summary of one object.",
     _schema({"object": _STRING}, required=["object"]), True),
    ("model_digest",
     "THE dimensional snapshot: every object with bounding box, volume, face/edge "
     "counts and placement, all spreadsheet aliases, and every sketch's constraints. "
     "Start here instead of measuring by hand.",
     _schema({"properties": {"type": "boolean",
                             "description": "Include full property dumps (verbose)."}}), True),
    ("feature_probe",
     "Functional geometry a bounding box cannot express: bores and bosses grouped by "
     "axis (direction, diameter, length, angular sweep, hole-vs-boss), planar faces "
     "with outward normals, and how far the body stands off its largest flat face.",
     _schema({"min_plane_area_mm2": _NUMBER, "tolerance": _NUMBER,
              "object": _STRING, "objects": _OBJECT_NAMES}), True),
    ("print_readiness",
     "FDM printability: overhang area per object for a build direction, plus the "
     "smallest bounding-box dimension.",
     _schema({"up_axis": {"type": "string", "enum": ["x", "y", "z", "-x", "-y", "-z"]},
              "overhang_deg": _NUMBER, "object": _STRING, "objects": _OBJECT_NAMES}), True),
    ("cross_section",
     "Numeric section through the solids: section area, closed-wire count and section "
     "bbox. This is how you prove a wall thickness or that a cavity exists.",
     _schema({"axis": {"type": "string", "enum": ["x", "y", "z"]}, "position": _NUMBER,
              "object": _STRING, "objects": _OBJECT_NAMES}), True),
    ("mass_properties", "Volume, centre of mass and (with a density) mass.",
     _schema({"density": {"type": "number",
                          "description": "kg/m3, e.g. 1270 for PETG."},
              "object": _STRING, "objects": _OBJECT_NAMES}), True),
    ("measure", "Minimum distance and closest points between two objects.",
     _schema({"object_a": _STRING, "object_b": _STRING}, required=["object_a", "object_b"]), True),
    ("check_solid", "Per object: solid count, validity, closedness — watertightness evidence.",
     _schema({"object": _STRING, "objects": _OBJECT_NAMES}), True),
    ("interference_check", "Do the given parts overlap? Volumes of any intersections.",
     _schema({"objects": _OBJECT_NAMES, "tolerance": _NUMBER}), True),
    ("validate", "Recompute-and-check: does the document report any errors?", _schema(), True),
    ("spreadsheet_info", "Cells, aliases and values of a Spreadsheet.",
     _schema({"object": _STRING}), True),
    ("sketch_info", "Constraints, datums and degrees of freedom of a Sketch.",
     _schema({"object": _STRING}), True),
    ("selection", "What is currently selected in the GUI.", _schema(with_document=False), True),
    ("history", "Undo/redo stack depth for a document.", _schema(), True),
    ("events", "Journal of document mutations the bridge observed.",
     _schema({"since": {"type": "integer"}}, with_document=False), True),
    ("list_checkpoints", "Saved checkpoints for a document.", _schema(), True),
    ("view_state", "Current camera state.", _schema(with_document=False), True),

    ("spreadsheet_set",
     "Set a Spreadsheet cell. THE preferred way to change a dimension: it drives the "
     "model instead of hard-coding a number into geometry.",
     _schema({"object": _STRING, "cell": _STRING, "value": {},
              "recompute": _BOOL}, required=["cell", "value"]), False),
    ("sketch_set_datum", "Change a dimensional constraint (datum) in a Sketch.",
     _schema({"object": _STRING, "constraint": {}, "value": _NUMBER,
              "recompute": _BOOL}, required=["object", "constraint", "value"]), False),
    ("set_property", "Set a plain property on an object.",
     _schema({"object": _STRING, "property": _STRING, "value": {}, "recompute": _BOOL},
             required=["object", "property", "value"]), False),
    ("set_expression", "Bind a property to a parametric expression.",
     _schema({"object": _STRING, "property": _STRING, "expression": _STRING,
              "recompute": _BOOL}, required=["object", "property", "expression"]), False),
    ("set_visibility", "Show or hide an object. Finish by hiding construction inputs.",
     _schema({"object": _STRING, "visible": _BOOL}, required=["object", "visible"]), False),
    ("create_object", "Add a native FreeCAD object.",
     _schema({"type_id": _STRING, "name": _STRING, "label": _STRING,
              "properties": {"type": "object"}}, required=["type_id"]), False),
    ("remove_object", "Delete an object.",
     _schema({"object": _STRING}, required=["object"]), False),
    ("recompute", "Recompute the document.", _schema(), False),
    ("batch",
     "Run several bridge commands as ONE undo transaction — use it when a change only "
     "makes sense as a unit.",
     _schema({"operations": {"type": "array", "items": {"type": "object"}},
              "label": _STRING, "checkpoint": _BOOL, "require_valid": _BOOL},
             required=["operations"]), False),
    ("execute_python",
     "Run Python inside FreeCAD, wrapped in a transaction. Last resort: prefer the "
     "structured commands above so the change stays inspectable.",
     _schema({"code": _STRING}, required=["code"], with_document=False), False),
    ("save", "Save the document in place.", _schema({"path": _STRING}), False),
    ("checkpoint", "Snapshot the document to a checkpoint file.",
     _schema({"label": _STRING}), False),
    ("open_checkpoint", "Reopen a checkpoint file.",
     _schema({"path": _STRING}, required=["path"], with_document=False), False),
    ("undo", "Undo N steps.", _schema({"count": {"type": "integer"}}), False),
    ("redo", "Redo N steps.", _schema({"count": {"type": "integer"}}), False),
    ("export", "Export objects to STL/STEP/... (format follows the file extension).",
     _schema({"path": _STRING, "objects": _OBJECT_NAMES}, required=["path"]), False),
    ("fit_view", "Frame the whole model in the viewport.", _schema(with_document=False), False),
    ("set_view", "Set the camera to a named orientation.",
     _schema({"view": _STRING, "orientation": _STRING, "fit": _BOOL}, with_document=False), False),
    ("screenshot", "Render the current view to a PNG.",
     _schema({"path": _STRING, "width": {"type": "integer"}, "height": {"type": "integer"}},
             required=["path"], with_document=False), False),
    ("selection_set", "Select objects in the GUI.", _schema({"objects": _OBJECT_NAMES}), False),
    ("view_restore", "Restore a previously captured camera state.",
     _schema({"state": {"type": "object"}}, required=["state"], with_document=False), False),
]

TOOLS_BY_NAME = {name: (description, schema, read_only)
                 for name, description, schema, read_only in TOOLS}


def tool_definitions():
    """The `tools/list` payload."""
    definitions = []
    for name, description, schema, read_only in TOOLS:
        definitions.append({
            "name": name,
            "description": description,
            "inputSchema": schema,
            # Advertise intent so a client can gate mutations; the bridge enforces
            # it regardless of what any client believes.
            "annotations": {"readOnlyHint": read_only, "destructiveHint": not read_only},
        })
    return definitions


def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_text(payload, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(
        payload, indent=2, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def negotiate_protocol(requested):
    """Answer with the client's revision when we can speak it, else with ours."""
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return PROTOCOL_VERSION


def handle_request(request, call_bridge, server_version="0.0.0"):
    """Handle one JSON-RPC message. Returns a response dict, or None for a
    notification (which by definition gets no reply).

    `call_bridge(command, args)` performs the actual bridge call and may raise;
    a raised exception becomes a tool result with isError=True rather than a
    protocol error, because it is the TOOL that failed, not the request — that
    distinction is what lets the model read the message and correct itself.
    """
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 request")

    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}
    is_notification = "id" not in request

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": negotiate_protocol(params.get("protocolVersion")),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": server_version},
            "instructions": (
                "Drives the FreeCAD document that is open in the AgentSmith panel. "
                "Start with `ping` and `model_digest`; use `feature_probe` for hole "
                "axes and mounting faces. Prefer `spreadsheet_set` over editing "
                "geometry directly, and finish with `validate` then `save`."),
        })

    if is_notification:
        return None  # initialized / cancelled / progress: nothing to answer

    if method == "tools/list":
        return _result(request_id, {"tools": tool_definitions()})

    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS_BY_NAME:
            # A wrong tool name is a protocol-level mistake, not a tool failure.
            return _error(request_id, INVALID_PARAMS, "unknown tool: %s" % name)
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, INVALID_PARAMS, "arguments must be an object")
        try:
            payload = call_bridge(name, arguments)
        except Exception as exc:
            return _result(request_id, _tool_text(
                "%s failed: %s" % (name, exc), is_error=True))
        return _result(request_id, _tool_text(payload))

    if method in ("ping", "$/ping"):
        return _result(request_id, {})

    return _error(request_id, METHOD_NOT_FOUND, "unsupported method: %s" % method)
