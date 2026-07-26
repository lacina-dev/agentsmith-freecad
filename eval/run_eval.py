#!/usr/bin/env python3
"""Offline grader for the FreeCAD modeling-agent harness.

Scores whatever FreeCAD model is CURRENTLY OPEN against a golden-task spec
(see eval/tasks/*.json), using only the read-only(-ish) commands exposed by
the FreeCAD AgentSmith bridge (see ../freecad_bridge_client.py and
../freecad_bridge/harness/*.md for the bridge protocol and conventions this
grader relies on). It never launches an AI backend — that requires the GUI —
it only asks the bridge questions about the model that is already loaded.

Usage:
    python3 eval/run_eval.py eval/tasks/m6_hex_nut.json
    python3 eval/run_eval.py --all
    python3 eval/run_eval.py --all --json
    python3 eval/run_eval.py --all --no-save                  # don't touch eval/results/history.jsonl
    python3 eval/run_eval.py eval/tasks/m6_hex_nut.json --discovery /path/to/other-bridge.json

Exit codes:
    0  every graded check passed
    1  at least one check failed (or errored)
    2  the bridge could not be reached at all — nothing was graded

See eval/README.md for the task JSON schema and how to add new tasks.
"""

import argparse
import glob
import json
import math
import os
import socket
import sys
from datetime import datetime, timezone

DISCOVERY_FILE = "/tmp/freecad-agentsmith-bridge.json"
TASKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks")
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
HISTORY_FILE = os.path.join(RESULTS_DIR, "history.jsonl")


class BridgeError(RuntimeError):
    """The bridge itself could not be reached (no discovery file, refused connection, ...)."""


class BridgeCallError(RuntimeError):
    """The bridge was reached but a specific command returned ok=false or raised."""


def bridge_call(command, args=None, timeout=10):
    """Send one request to the FreeCAD AgentSmith bridge and return its 'result'.

    Raises BridgeError if the bridge itself is unreachable, or BridgeCallError
    if the bridge responded but the command failed. Callers that want a
    per-check "error" outcome instead of a crash should catch BridgeCallError;
    BridgeError is intended to abort the whole run (bridge unreachable).
    """
    try:
        with open(DISCOVERY_FILE, "r", encoding="utf-8") as handle:
            discovery = json.load(handle)
    except FileNotFoundError:
        raise BridgeError(
            "Discovery file not found: %s\n"
            "Open FreeCAD with a model loaded and make sure the Bridge panel/add-in "
            "is running (it writes this file when its socket server starts)." % DISCOVERY_FILE
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError("Could not read discovery file %s: %s" % (DISCOVERY_FILE, exc))

    for key in ("host", "port", "token"):
        if key not in discovery:
            raise BridgeError("Discovery file %s is missing %r" % (DISCOVERY_FILE, key))

    request = {"token": discovery["token"], "command": command, "args": args or {}}
    try:
        with socket.create_connection((discovery["host"], discovery["port"]), timeout=timeout) as connection:
            connection.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response = b""
            while b"\n" not in response:
                block = connection.recv(65536)
                if not block:
                    break
                response += block
    except (OSError, socket.timeout) as exc:
        raise BridgeError(
            "Could not connect to bridge at %s:%s (%s).\n"
            "Open FreeCAD with the Bridge panel and a model loaded, then retry."
            % (discovery.get("host"), discovery.get("port"), exc)
        )

    if not response:
        raise BridgeError("Bridge closed the connection without responding to %r." % command)

    try:
        parsed = json.loads(response.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("Bridge returned an unparseable response for %r: %s" % (command, exc))

    if not parsed.get("ok"):
        raise BridgeCallError(str(parsed.get("error", "unknown bridge error")))
    return parsed.get("result")


# --------------------------------------------------------------------------
# Small result helpers. Every check produces one of these dicts.
# --------------------------------------------------------------------------

def _bool_check(name, expected, actual, note=""):
    status = "pass" if bool(actual) == bool(expected) else "fail"
    return {"check": name, "status": status, "expected": expected, "actual": actual, "note": note}


def _numeric_check(name, expected, actual, tol, note=""):
    try:
        diff = abs(float(actual) - float(expected))
    except (TypeError, ValueError):
        return {"check": name, "status": "error", "expected": expected, "actual": actual,
                "note": (note + "; " if note else "") + "measured value is not numeric"}
    # The rule is "within tol", so a value exactly AT the tolerance must pass. Binary
    # floats disagree: abs(22.0 - 21.9) is 0.10000000000000142, which failed a
    # tol=0.1 check on a model that was dimensionally fine (seen live). The epsilon
    # is far below any real modelling tolerance and only absorbs representation noise.
    status = "pass" if diff <= tol + 1e-9 else "fail"
    full_note = "diff=%.6g (tol=%.6g)" % (diff, tol)
    if note:
        full_note = note + "; " + full_note
    return {"check": name, "status": status, "expected": expected, "actual": actual, "note": full_note}


def _error_check(name, expected, message):
    return {"check": name, "status": "error", "expected": expected, "actual": None, "note": message}


# --------------------------------------------------------------------------
# model_digest helpers
# --------------------------------------------------------------------------

class DigestCache:
    """Fetches model_digest at most once per task run and memoizes the outcome
    (including failure), so N dimension checks cost one bridge round trip."""

    def __init__(self):
        self._fetched = False
        self._digest = None
        self._error = None

    def get(self):
        if not self._fetched:
            self._fetched = True
            try:
                self._digest = bridge_call("model_digest")
            except BridgeCallError as exc:
                self._error = str(exc)
        if self._error is not None:
            raise BridgeCallError(self._error)
        return self._digest


def _bbox_size(digest, axis, object_name=None):
    """Bounding-box size along 'x'/'y'/'z', or 'max'/'min' for the largest/smallest
    extent regardless of model-space orientation (orientation-agnostic checks)."""
    axis_index = {"x": 0, "y": 1, "z": 2}.get(axis)
    if axis_index is None and axis not in ("max", "min"):
        raise ValueError("unknown axis %r (expected x, y, z, max or min)" % axis)
    if object_name:
        for obj in digest.get("objects", []):
            if obj.get("name") == object_name or obj.get("label") == object_name:
                bbox = obj.get("bounding_box")
                if not bbox:
                    raise ValueError("object %r has no bounding box (null or invisible shape)" % object_name)
                size = bbox["size"]
                break
        else:
            raise ValueError("object %r not found in model_digest" % object_name)
    else:
        overall = digest.get("overall_bounding_box")
        if not overall:
            raise ValueError("model_digest has no overall_bounding_box (no visible geometry)")
        size = [overall["max"][i] - overall["min"][i] for i in range(3)]
    if axis == "max":
        return max(size)
    if axis == "min":
        return min(size)
    return size[axis_index]


def _alias_numeric(value):
    """model_digest reports quantity cells as {'value': 8.0, 'unit': .., 'text': ..};
    plain numbers stay plain. Normalize both to a float, or None if not numeric."""
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _find_alias(digest, alias):
    """Return (sheet_object_name, cell_ref, numeric_value_or_raw) for an alias."""
    for sheet_name, cells in digest.get("spreadsheets", {}).items():
        for cell_ref, info in cells.items():
            if info.get("alias") == alias:
                raw = info.get("value")
                numeric = _alias_numeric(raw)
                return sheet_name, cell_ref, numeric if numeric is not None else raw
    raise ValueError("alias %r not found in any Spreadsheet" % alias)


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------

def check_validate(expected):
    try:
        result = bridge_call("validate")
    except BridgeCallError as exc:
        return _error_check("validate", expected, str(exc))
    actual_ok = bool(result.get("ok"))
    note = "" if actual_ok else "errors: %s" % result.get("errors")
    return _bool_check("validate", expected, actual_ok, note)


def check_solid_flags(checks):
    """Handles the 'watertight', 'single_solid' and 'solid_count' checks together,
    since all three read from a single check_solid call."""
    results = []
    try:
        solid_result = bridge_call("check_solid")
        objects = solid_result.get("objects", [])
        error = None
    except BridgeCallError as exc:
        objects = []
        error = str(exc)

    if "watertight" in checks:
        expected = checks["watertight"]
        if error:
            results.append(_error_check("watertight", expected, error))
        else:
            actual = bool(objects) and all(o.get("is_valid") and o.get("is_closed") for o in objects)
            note = "; ".join(
                "%s: valid=%s closed=%s" % (o.get("label", o.get("name")), o.get("is_valid"), o.get("is_closed"))
                for o in objects
            ) or "no solids found in model"
            results.append(_bool_check("watertight", expected, actual, note))

    if "single_solid" in checks:
        expected = checks["single_solid"]
        if error:
            results.append(_error_check("single_solid", expected, error))
        else:
            actual = bool(objects) and all(o.get("solid_count") == 1 for o in objects)
            note = "; ".join(
                "%s: solids=%s" % (o.get("label", o.get("name")), o.get("solid_count")) for o in objects
            ) or "no solids found in model"
            results.append(_bool_check("single_solid", expected, actual, note))

    if "solid_count" in checks:
        # 'solid_count' asserts the model is made of exactly N separate solid
        # objects (e.g. a two-part assembly on the plate). We count the number of
        # visible objects whose shape contains at least one solid — objects that
        # are pure sketches/wires/datums (solid_count == 0) are not counted.
        # This is the deliberate complement of 'single_solid': single_solid says
        # "every object is exactly one solid"; solid_count says "there are exactly
        # this many solid objects in the document".
        spec = checks["solid_count"]
        expected = spec.get("expected") if isinstance(spec, dict) else spec
        if error:
            results.append(_error_check("solid_count", expected, error))
        else:
            actual = sum(1 for o in objects if (o.get("solid_count") or 0) >= 1)
            note = "%d of %d visible object(s) contain >=1 solid" % (actual, len(objects))
            detail = "; ".join(
                "%s: solids=%s" % (o.get("label", o.get("name")), o.get("solid_count")) for o in objects
            )
            if detail:
                note += " (" + detail + ")"
            try:
                status = "pass" if int(actual) == int(expected) else "fail"
            except (TypeError, ValueError):
                results.append(_error_check("solid_count", expected,
                                            "solid_count 'expected' is not an integer: %r" % (expected,)))
            else:
                results.append({"check": "solid_count", "status": status,
                                "expected": expected, "actual": actual, "note": note})

    return results


def check_dimension(dim, digest_cache):
    name = dim.get("name", "dimension")
    expected = dim.get("expected")
    tol = dim.get("tol", 1e-6)
    source = dim.get("source")

    try:
        digest = digest_cache.get()
    except BridgeCallError as exc:
        return _error_check(name, expected, "model_digest failed: %s" % exc)

    try:
        if source == "bbox":
            axis = dim.get("axis", "x")
            measured = _bbox_size(digest, axis, dim.get("object"))
            note = "bbox size, axis=%s, object=%s" % (axis, dim.get("object") or "<overall model>")
        elif source == "alias":
            alias = dim.get("alias")
            if not alias:
                return _error_check(name, expected, "dimension check missing 'alias'")
            _, _, measured = _find_alias(digest, alias)
            note = "spreadsheet alias=%s" % alias
        else:
            return _error_check(name, expected, "unknown dimension source %r (expected 'bbox' or 'alias')" % source)
    except ValueError as exc:
        return _error_check(name, expected, str(exc))

    # Some requirements are one-sided and a centred window misstates them. "A seat at
    # least 8 mm wide" is a floor, not a target: a 30 mm seat satisfies the request but
    # failed an `expected 10, tol 3` window (seen live). Likewise "sized undersize for
    # a press fit" is a ceiling — a nominal 22.0 mm bore is not a press fit, yet it sits
    # inside a +/-0.1 window around 21.9.
    mode = str(dim.get("mode", "window")).lower()
    if mode in ("min", "max"):
        try:
            measured_value = float(measured)
            limit = float(expected)
        except (TypeError, ValueError):
            return {"check": name, "status": "error", "expected": expected,
                    "actual": measured, "note": note + "; measured value is not numeric"}
        ok = measured_value >= limit - 1e-9 if mode == "min" else measured_value <= limit + 1e-9
        return {"check": name, "status": "pass" if ok else "fail",
                "expected": {"mode": mode, "limit": limit}, "actual": measured_value,
                "note": "%s; %s %.6g (required %s %.6g)" % (
                    note, "measured" if ok else "measured", measured_value,
                    ">=" if mode == "min" else "<=", limit)}
    if mode != "window":
        return _error_check(name, expected,
                            "unknown dimension mode %r (expected 'window', 'min' or 'max')" % mode)

    return _numeric_check(name, expected, measured, tol, note)


def run_parametric_probe(probe):
    """Nudge a Spreadsheet alias by delta, confirm the model still validates,
    then ALWAYS restore the original value (finally), even on failure."""
    alias = probe.get("alias")
    delta = probe.get("delta", 0.0)
    name = "parametric_probe(%s)" % alias
    expected = "validate.ok after %s%s" % ("+" if delta >= 0 else "", delta)

    if not alias:
        return _error_check(name, expected, "parametric_probe missing 'alias'")

    try:
        digest = bridge_call("model_digest")
    except BridgeCallError as exc:
        return _error_check(name, expected, "model_digest failed: %s" % exc)

    try:
        sheet_name, cell_ref, original_value = _find_alias(digest, alias)
    except ValueError as exc:
        return _error_check(name, expected, str(exc))

    if not isinstance(original_value, (int, float)) or isinstance(original_value, bool):
        return _error_check(name, expected, "alias %r value %r is not numeric" % (alias, original_value))

    result = None
    restored = True
    try:
        new_value = original_value + delta
        bridge_call("spreadsheet_set", {
            "object": sheet_name, "cell": cell_ref, "value": new_value, "recompute": True,
        })
        validate_after = bridge_call("validate")
        probe_ok = bool(validate_after.get("ok"))
        note = "set %s: %s -> %s, then validate.ok=%s" % (alias, original_value, new_value, probe_ok)
        if not probe_ok:
            note += "; errors: %s" % validate_after.get("errors")
        status = "pass" if probe_ok else "fail"
        result = {"check": name, "status": status, "expected": expected, "actual": probe_ok, "note": note}
    except BridgeCallError as exc:
        result = _error_check(name, expected, "probe mutation failed: %s" % exc)
    finally:
        try:
            bridge_call("spreadsheet_set", {
                "object": sheet_name, "cell": cell_ref, "value": original_value, "recompute": True,
            })
        except BridgeCallError as restore_exc:
            restored = False
            print(
                "WARNING: failed to restore alias %r (%s!%s) to its original value %r: %s\n"
                "         The open model may be left mutated — restore it manually."
                % (alias, sheet_name, cell_ref, original_value, restore_exc),
                file=sys.stderr,
            )

    if not restored and result is not None:
        result["note"] = result.get("note", "") + "; WARNING: restore-on-cleanup failed, model may be mutated"
    return result


def check_print_readiness(spec):
    """Grade the 'print_readiness' check type.

    Calls the (possibly unsupported) bridge command 'print_readiness', which
    reports per-object overhang area and minimum feature size for a given
    print-up axis and overhang threshold. Aggregates across every object that
    didn't error, and passes if the overall overhang area percentage is within
    budget and (if requested) every object's thinnest bounding-box dimension
    clears a minimum feature size.

    Older bridges that don't implement this command yet respond with an
    "Unknown command: ..." error; that is NOT treated as a failure or crash —
    it produces a "skipped" status so upgrading run_eval.py doesn't retroactively
    fail scorecards graded against an older bridge build.
    """
    name = "print_readiness"
    max_pct = spec.get("max_overhang_area_pct")
    min_feature = spec.get("min_feature_mm")

    expected = {}
    if max_pct is not None:
        expected["max_overhang_area_pct"] = max_pct
    if min_feature is not None:
        expected["min_feature_mm"] = min_feature

    call_args = {}
    if "up_axis" in spec:
        call_args["up_axis"] = spec["up_axis"]
    if "overhang_deg" in spec:
        call_args["overhang_deg"] = spec["overhang_deg"]

    try:
        result = bridge_call("print_readiness", call_args)
    except BridgeCallError as exc:
        message = str(exc)
        if "unknown command" in message.lower():
            return {
                "check": name,
                "status": "skipped",
                "expected": expected,
                "actual": None,
                "note": "bridge does not implement 'print_readiness' (older bridge build) "
                        "— skipped, not counted as a failure",
            }
        return _error_check(name, expected, message)

    objects = (result or {}).get("objects", []) or []
    ok_objects = [o for o in objects if not o.get("error")]
    bad_objects = [o for o in objects if o.get("error")]

    if not ok_objects:
        note = "no usable print_readiness data returned"
        if bad_objects:
            note += "; object errors: %s" % ", ".join(
                "%s: %s" % (o.get("object", "?"), o.get("error")) for o in bad_objects
            )
        return _error_check(name, expected, note)

    total_face_area = sum(float(o.get("total_face_area") or 0.0) for o in ok_objects)
    total_overhang_area = sum(float(o.get("overhang_area") or 0.0) for o in ok_objects)
    overhang_pct = (total_overhang_area / total_face_area * 100.0) if total_face_area > 0 else 0.0

    min_dims = [float(o["min_bbox_dim"]) for o in ok_objects if o.get("min_bbox_dim") is not None]
    worst_min_dim = min(min_dims) if min_dims else None

    pct_ok = max_pct is None or overhang_pct <= max_pct
    if min_feature is None:
        feature_ok = True
    else:
        feature_ok = worst_min_dim is not None and worst_min_dim >= min_feature

    status = "pass" if (pct_ok and feature_ok) else "fail"

    actual = {
        "overhang_area_pct": round(overhang_pct, 4),
        "min_bbox_dim": (round(worst_min_dim, 4) if worst_min_dim is not None else None),
        "objects_graded": len(ok_objects),
        "objects_errored": len(bad_objects),
    }

    note_parts = ["%d object(s) graded" % len(ok_objects)]
    if bad_objects:
        note_parts.append("%d object(s) errored: %s" % (
            len(bad_objects),
            ", ".join("%s: %s" % (o.get("object", "?"), o.get("error")) for o in bad_objects),
        ))
    if max_pct is not None:
        note_parts.append("overhang_area_pct=%.4g%% (max %.4g%%)" % (overhang_pct, max_pct))
    if min_feature is not None:
        note_parts.append("min_bbox_dim=%s (required >= %.4g)" % (
            ("%.4g" % worst_min_dim) if worst_min_dim is not None else "n/a", min_feature
        ))
    note = "; ".join(note_parts)

    return {"check": name, "status": status, "expected": expected, "actual": actual, "note": note}


class ProbeCache:
    """Fetches feature_probe at most once per task run and memoizes the outcome,
    the same way DigestCache does for model_digest."""

    def __init__(self):
        self._fetched = False
        self._probe = None
        self._error = None

    def get(self):
        if not self._fetched:
            self._fetched = True
            try:
                self._probe = bridge_call("feature_probe")
            except BridgeCallError as exc:
                self._error = str(exc)
        if self._error is not None:
            raise BridgeCallError(self._error)
        return self._probe


def _skipped_check(name, expected, command):
    return {
        "check": name,
        "status": "skipped",
        "expected": expected,
        "actual": None,
        "note": "bridge does not implement %r (older bridge build) — skipped, "
                "not counted as a failure" % command,
    }


def _axis_vector(axis):
    """'x'/'y'/'z' (optionally signed) as a unit vector. Sign is irrelevant to every
    functional check here — an axis has an orientation, not a direction."""
    letter = str(axis).lower().lstrip("+-")
    index = {"x": 0, "y": 1, "z": 2}.get(letter)
    if index is None:
        raise ValueError("unknown axis %r (expected x, y or z)" % axis)
    vector = [0.0, 0.0, 0.0]
    vector[index] = 1.0
    return vector, index


def _angle_between_axes_deg(a, b):
    """Angle between two undirected axes, in [0, 90]."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-9 or norm_b < 1e-9:
        raise ValueError("degenerate axis vector")
    cosine = max(-1.0, min(1.0, abs(dot) / (norm_a * norm_b)))
    return math.degrees(math.acos(cosine))


def _mounting_reference(spec, probe):
    """Resolve the mounting plane the functional checks are measured against.

    Returns (normal_vector, standoff_mm_or_None, description).

    Default is `mounting_axis: "auto"`: take the largest planar face across the
    graded objects as the mounting/contact surface and use its normal. This keeps
    the checks orientation-agnostic, which matters because a golden task rarely
    pins the model's pose in world space — printed_wall_hook is explicitly modelled
    lying on its side for printing, so a check hard-coded to the Y axis would flag
    a perfectly good part. A task that *does* pin the pose can still name an axis.
    """
    axis = str(spec.get("mounting_axis", "auto")).lower()
    if axis != "auto":
        vector, _index = _axis_vector(axis)
        return vector, None, "the %s axis" % axis

    best = None
    for obj in (probe or {}).get("objects", []):
        if obj.get("error"):
            continue
        plane = obj.get("largest_plane")
        if plane and (best is None or plane.get("area_mm2", 0) > best.get("area_mm2", 0)):
            best = plane
    if not best:
        raise ValueError("no planar face found to use as the mounting surface "
                         "(set mounting_axis explicitly if the part has none)")
    return (best["normal"], best.get("standoff_mm"),
            "the normal of the largest flat face (%.4g mm2)" % best.get("area_mm2", 0.0))


def check_functional(spec, digest_cache, probe_cache):
    """Grade the 'functional' block: does the part physically work, not just measure?

    Born from a real failure (harness lesson L1/L2): a wall hook scored 7/7 on
    dimensions while being an unusable flat plate, because every check looked at
    sizes and none looked at the functional pose. These checks are the mechanical
    part of that judgement, so it no longer depends on an LLM reviewer noticing.

    `mounting_axis` is the axis normal to the mounting/contact plane — for a part
    screwed to a wall, the axis pointing away from the wall. Sub-checks:

      * `hole_axis_tol_deg`  — fastener holes must be PARALLEL to that axis, i.e.
        perpendicular to the mounting plane, or the screws cannot go in (lesson L1).
      * `protrusion_mm`      — the part must extend at least this far along that
        axis, or it is a flat lookalike built in the wrong projection (lesson L2).
      * `min_slab_ratio`     — thinnest bbox dimension over the largest; a cheap,
        pose-independent detector for the same flat-plate failure.

    Each sub-check present in the spec produces its own scorecard row. Returns a
    list of check dicts.
    """
    results = []
    wanted = [key for key in ("hole_axis_tol_deg", "protrusion_mm", "min_slab_ratio")
              if key in spec]
    if not wanted:
        return [_error_check("functional", spec,
                             "no functional sub-check requested (expected at least one of "
                             "hole_axis_tol_deg, protrusion_mm, min_slab_ratio)")]

    # One probe call serves every sub-check; a bridge that predates feature_probe
    # skips them all rather than failing the scorecard.
    axis_vector = standoff = reference_note = None
    probe_error = None
    if "hole_axis_tol_deg" in spec or "protrusion_mm" in spec:
        try:
            probe = probe_cache.get()
            axis_vector, standoff, reference_note = _mounting_reference(spec, probe)
        except BridgeCallError as exc:
            probe_error = ("skipped" if "unknown command" in str(exc).lower() else str(exc))
        except ValueError as exc:
            probe_error = str(exc)

    if "hole_axis_tol_deg" in spec:
        name = "functional_hole_axes"
        tol_deg = float(spec["hole_axis_tol_deg"])
        min_dia = float(spec.get("min_hole_dia_mm", 2.0))
        max_dia = spec.get("max_hole_dia_mm")
        min_count = int(spec.get("min_hole_count", 1))
        # A fastener bore sweeps most of a circle; a curved lip, a rounded slot end
        # or a filleted corner is concave too but sweeps far less. Both numbers below
        # are measured, not guessed: a wall hook's J-curves came back at 90 deg, and
        # its TEARDROP screw bores — the self-supporting shape the print3d playbook
        # asks for — at exactly 270 deg. A threshold of 300 would have rejected the
        # better design, so it sits at 240: clear of the lip, clear of the teardrop.
        min_angle = float(spec.get("min_hole_angle_deg", 240.0))
        expected = {"mounting_axis": spec.get("mounting_axis", "auto"), "tol_deg": tol_deg,
                    "min_hole_count": min_count, "min_hole_dia_mm": min_dia,
                    "min_hole_angle_deg": min_angle}
        if max_dia is not None:
            expected["max_hole_dia_mm"] = float(max_dia)
        if probe_error == "skipped":
            results.append(_skipped_check(name, expected, "feature_probe"))
        elif probe_error:
            results.append(_error_check(name, expected, probe_error))
        else:
            # A diameter window selects the fasteners specifically: a bearing block
            # has a 22 mm bore whose axis is deliberately NOT perpendicular to the
            # base, and grading it as a screw hole would be nonsense.
            holes = [hole
                     for obj in (probe or {}).get("objects", []) if not obj.get("error")
                     for hole in obj.get("holes", [])
                     if hole.get("kind") == "hole"
                     and float(hole.get("diameter_mm", 0)) >= min_dia
                     and (max_dia is None or float(hole.get("diameter_mm", 0)) <= float(max_dia))
                     # angle_deg is absent on bridges older than 0.18.1; treat a
                     # missing value as "full circle" so those still grade.
                     and float(hole.get("angle_deg", 360.0)) >= min_angle]
            misaligned = []
            for hole in holes:
                try:
                    angle = _angle_between_axes_deg(hole["axis"], axis_vector)
                except (ValueError, KeyError):
                    continue
                if angle > tol_deg:
                    misaligned.append((round(float(hole.get("diameter_mm", 0)), 3), round(angle, 2)))
            actual = {"holes_found": len(holes), "misaligned": len(misaligned)}
            if len(holes) < min_count:
                status = "fail"
                note = ("only %d hole(s) of diameter >= %.4g mm found, need %d — "
                        "fastener holes missing or modelled as something other than a bore"
                        % (len(holes), min_dia, min_count))
            elif misaligned:
                status = "fail"
                note = ("%d hole(s) not perpendicular to the mounting plane "
                        "(dia mm, angle deg from %s): %s"
                        % (len(misaligned), reference_note, misaligned))
            else:
                status = "pass"
                note = "%d hole(s) within %.4g deg of %s" % (
                    len(holes), tol_deg, reference_note)
            results.append({"check": name, "status": status, "expected": expected,
                            "actual": actual, "note": note})

    if "protrusion_mm" in spec:
        name = "functional_protrusion"
        required = float(spec["protrusion_mm"])
        expected = {"axis": spec.get("mounting_axis", "auto"), "min_mm": required}
        extent = None
        error = None
        if probe_error == "skipped":
            results.append(_skipped_check(name, expected, "feature_probe"))
        elif probe_error:
            results.append(_error_check(name, expected, probe_error))
        else:
            if standoff is not None:
                # Auto mode: distance from the mounting face to the farthest point.
                extent = float(standoff)
            else:
                # Explicit axis: the bounding-box extent along it is the same thing
                # for an axis-aligned mounting plane.
                try:
                    index = axis_vector.index(1.0)
                    extent = _bbox_size(digest_cache.get(), "xyz"[index], spec.get("object"))
                except (BridgeCallError, ValueError) as exc:
                    error = str(exc)
            if error is not None:
                results.append(_error_check(name, expected, error))
            else:
                status = "pass" if extent >= required else "fail"
                note = ("stands off %s by %.4g mm (need >= %.4g mm)%s"
                        % (reference_note, extent, required,
                           "" if status == "pass" else
                           " — the working feature does not stand off the mounting "
                           "surface; likely built in the wrong projection plane"))
                results.append({"check": name, "status": status, "expected": expected,
                                "actual": round(extent, 4), "note": note})

    if "min_slab_ratio" in spec:
        name = "functional_slab_ratio"
        required = float(spec["min_slab_ratio"])
        expected = {"min_ratio": required}
        try:
            digest = digest_cache.get()
            smallest = _bbox_size(digest, "min", spec.get("object"))
            largest = _bbox_size(digest, "max", spec.get("object"))
        except (BridgeCallError, ValueError) as exc:
            results.append(_error_check(name, expected, str(exc)))
        else:
            ratio = (smallest / largest) if largest else 0.0
            status = "pass" if ratio >= required else "fail"
            note = "thinnest/largest bbox dimension = %.4g (need >= %.4g)%s" % (
                ratio, required,
                "" if status == "pass" else " — the part is a flat slab")
            results.append({"check": name, "status": status, "expected": expected,
                            "actual": round(ratio, 4), "note": note})

    return results


# --------------------------------------------------------------------------
# Task grading + reporting
# --------------------------------------------------------------------------

def grade_task(task):
    checks = task.get("checks", {})
    results = []

    if "validate" in checks:
        results.append(check_validate(checks["validate"]))

    if "watertight" in checks or "single_solid" in checks or "solid_count" in checks:
        results.extend(check_solid_flags(checks))

    digest_cache = DigestCache()
    for dim in checks.get("dimensions", []):
        results.append(check_dimension(dim, digest_cache))

    if "parametric_probe" in checks:
        results.append(run_parametric_probe(checks["parametric_probe"]))

    if "print_readiness" in checks:
        results.append(check_print_readiness(checks["print_readiness"]))

    # Functional pose last: it is the check that decides whether a model that
    # measures correctly can actually do its job (harness lessons L1/L2).
    if "functional" in checks:
        results.extend(check_functional(checks["functional"], digest_cache, ProbeCache()))

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    errors = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    graded = len(results) - skipped
    return {
        "task_id": task.get("id"),
        "title": task.get("title", task.get("id", "task")),
        "prompt": task.get("prompt", ""),
        "results": results,
        "passed_count": passed,
        "failed_count": failed,
        "error_count": errors,
        "skipped_count": skipped,
        "total_count": len(results),
        "graded_count": graded,
        # Skipped checks (e.g. print_readiness against an older bridge) are
        # neither passes nor failures: they don't count against all_passed,
        # and therefore don't affect run_eval.py's exit code either.
        "all_passed": bool(results) and failed == 0 and errors == 0,
    }


def save_history(report):
    """Append one JSON line per graded task to eval/results/history.jsonl."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    entry = {
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "task_id": report.get("task_id"),
        "score": "%d/%d" % (report["passed_count"], report["graded_count"]),
        "passed": report["passed_count"],
        "failed": report["failed_count"],
        "errors": report["error_count"],
        "skipped": report["skipped_count"],
        "checks": [
            {"name": r["check"], "status": r["status"], "expected": r.get("expected"), "actual": r.get("actual")}
            for r in report["results"]
        ],
    }
    with open(HISTORY_FILE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


_STATUS_SYMBOL = {"pass": "✓", "fail": "✗", "error": "⚠", "skipped": "⚠"}


def format_markdown(report):
    lines = []
    lines.append("## %s" % report["title"])
    if report.get("prompt"):
        lines.append("")
        lines.append("_Prompt: %s_" % report["prompt"])
    lines.append("")
    lines.append("| Check | | Expected | Actual | Note |")
    lines.append("|---|---|---|---|---|")
    for r in report["results"]:
        symbol = _STATUS_SYMBOL.get(r["status"], "?")
        note = str(r.get("note", "")).replace("|", "/").replace("\n", " ")
        lines.append("| %s | %s | %s | %s | %s |" % (r["check"], symbol, r.get("expected"), r.get("actual"), note))
    lines.append("")
    skipped = report.get("skipped_count", 0)
    suffix = " (%d skipped)" % skipped if skipped else ""
    lines.append("**Score: %d/%d checks passed%s**" % (report["passed_count"], report["graded_count"], suffix))
    return "\n".join(lines)


def load_task(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    global DISCOVERY_FILE
    parser = argparse.ArgumentParser(
        prog="run_eval.py",
        description=(
            "Grade the currently open FreeCAD model against a golden-task spec, "
            "via the FreeCAD AgentSmith bridge. Does NOT build or launch anything: "
            "open/build the model in FreeCAD yourself first, then grade it."
        ),
    )
    parser.add_argument("task", nargs="?", help="Path to a single task JSON file (eval/tasks/*.json)")
    parser.add_argument("--all", action="store_true", help="Grade every eval/tasks/*.json")
    parser.add_argument("--json", action="store_true", help="Emit a single machine-readable JSON object instead of Markdown")
    parser.add_argument(
        "--discovery", default=DISCOVERY_FILE, metavar="PATH",
        help="Path to the bridge discovery JSON file (default: %(default)s). "
             "Mainly useful for testing the 'bridge unreachable' path against a fake/nonexistent file.",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Don't append graded results to eval/results/history.jsonl",
    )
    args = parser.parse_args(argv)

    DISCOVERY_FILE = args.discovery

    if not args.all and not args.task:
        parser.error("provide a path to a task JSON file, or use --all")

    try:
        bridge_call("ping")
    except BridgeError as exc:
        print("ERROR: FreeCAD bridge unreachable — nothing was graded.\n\n%s" % exc, file=sys.stderr)
        return 2
    except BridgeCallError as exc:
        print("ERROR: FreeCAD bridge responded with an error to 'ping': %s\n"
              "Open FreeCAD with the Bridge panel and a model loaded, then retry." % exc, file=sys.stderr)
        return 2

    if args.all:
        task_paths = sorted(glob.glob(os.path.join(TASKS_DIR, "*.json")))
        if not task_paths:
            print("No task files found in %s" % TASKS_DIR, file=sys.stderr)
            return 2
    else:
        task_paths = [args.task]

    reports = []
    load_failed = False
    for path in task_paths:
        try:
            task = load_task(path)
        except (OSError, json.JSONDecodeError) as exc:
            print("ERROR: could not load task %s: %s" % (path, exc), file=sys.stderr)
            load_failed = True
            continue
        reports.append(grade_task(task))

    if not args.no_save:
        for report in reports:
            save_history(report)

    if args.json:
        payload = reports if args.all else (reports[0] if reports else None)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for index, report in enumerate(reports):
            if index:
                print("\n---\n")
            print(format_markdown(report))
        if reports:
            total_passed = sum(r["passed_count"] for r in reports)
            total_graded = sum(r["graded_count"] for r in reports)
            total_skipped = sum(r["skipped_count"] for r in reports)
            if len(reports) > 1:
                print("\n---\n")
                suffix = " (%d skipped)" % total_skipped if total_skipped else ""
                print("### Overall: %d/%d checks passed across %d task(s)%s" % (
                    total_passed, total_graded, len(reports), suffix))

    all_passed = bool(reports) and all(r["all_passed"] for r in reports) and not load_failed
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
