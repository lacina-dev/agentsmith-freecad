"""Decision logic for the review loop: when to try again, and what to hand the reviewer.

Extracted from BridgeGui so it can be unit-tested without FreeCAD or Qt. This
module MUST NOT import FreeCAD, FreeCADGui, Part or PySide.

Two jobs:

* ``autofix_decision`` — the corrective loop used to be hard-wired to exactly one
  round, chosen for safety rather than because one is the right number. One round
  is too few when a fix reveals the next problem, and any fixed N is too many when
  the model simply cannot fix a thing: it burns a full task budget re-reporting the
  same complaint. So the loop runs until the findings stop changing, which is the
  real signal — and the number of rounds only bounds the damage.
* ``format_precheck_report`` — everything a machine can check (validity, solidity,
  hole axes, standoff, overhangs) measured deterministically BEFORE the reviewer
  runs, so the LLM pass is spent on what only it can judge: does this match the
  request and the reference.
"""

import re

VERDICT_CONCERNS = "VERDICT: CONCERNS"
VERDICT_PASS = "VERDICT: PASS"

# Default ceiling on corrective rounds. Convergence normally stops the loop first;
# this only bounds a pathological case where every round produces new complaints.
DEFAULT_MAX_ROUNDS = 2

# How much of two rounds' findings must overlap before we call it "the same
# complaint again". Not 1.0: a reviewer rarely repeats itself word for word, and
# the numbers move even when the underlying defect does not.
CONVERGENCE_SIMILARITY = 0.6

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")
_NUMBER = re.compile(r"[-+]?\d*[.,]?\d+")
_NOISE = re.compile(r"[^a-z ]+")


def normalize_findings(verdict_text):
    """The reviewer's findings as a comparable set of normalized phrases.

    Numbers are stripped deliberately: "wall is 1.8 mm, needs 2.0" and "wall is
    1.9 mm, needs 2.0" are the same unfixed defect, and comparing the digits would
    read a failed fix as progress.
    """
    findings = set()
    for line in (verdict_text or "").splitlines():
        match = _BULLET.match(line)
        if not match:
            continue
        text = match.group(1).lower()
        if text.startswith(("suggested fix", "verdict")):
            continue
        text = _NUMBER.sub(" ", text)
        text = _NOISE.sub(" ", text)
        words = [word for word in text.split() if len(word) > 2]
        if words:
            findings.add(" ".join(words))
    return findings


def findings_similarity(previous, current):
    """Jaccard overlap of two finding sets; 1.0 for identical, 0.0 for disjoint."""
    if not previous or not current:
        return 0.0
    intersection = len(previous & current)
    union = len(previous | current)
    return intersection / union if union else 0.0


def autofix_decision(verdict_text, previous_findings=None, rounds_done=0,
                     max_rounds=DEFAULT_MAX_ROUNDS, enabled=True, busy=False,
                     similarity=CONVERGENCE_SIMILARITY):
    """Should another corrective round run?

    Returns ``{"run", "reason", "findings", "converged"}``. ``reason`` is written
    to be shown to the user as-is — a loop that stops without saying why looks
    like a bug.
    """
    findings = normalize_findings(verdict_text)
    result = {"run": False, "reason": "", "findings": findings, "converged": False}

    if not enabled:
        result["reason"] = "auto-fix is off"
        return result
    if busy:
        result["reason"] = "another task is running"
        return result
    if VERDICT_CONCERNS not in (verdict_text or ""):
        result["reason"] = "reviewer raised no concerns"
        return result
    if rounds_done >= max_rounds:
        result["reason"] = ("corrective round limit reached (%d) — the remaining findings "
                            "need a human" % max_rounds)
        return result

    overlap = findings_similarity(previous_findings or set(), findings)
    if overlap >= similarity:
        result["converged"] = True
        result["reason"] = ("the reviewer is repeating the previous findings (%.0f%% the "
                            "same) — another round would not fix them" % (overlap * 100))
        return result

    result["run"] = True
    result["reason"] = ("round %d of %d" % (rounds_done + 1, max_rounds)) if not previous_findings \
        else ("round %d of %d — findings changed (%.0f%% overlap)"
              % (rounds_done + 1, max_rounds, overlap * 100))
    return result


# Read-only commands whose answers a reviewer would otherwise have to gather by
# hand, one tool call at a time, out of its own budget.
PRECHECK_COMMANDS = ("validate", "check_solid", "model_digest", "feature_probe",
                     "print_readiness")


def format_precheck_report(results):
    """Compact, factual summary of the deterministic checks for the reviewer prompt.

    ``results`` maps command name to either the bridge result or an Exception. A
    failed command is reported as such rather than dropped: "print_readiness could
    not run" is information the reviewer needs, and silence would read as "fine".
    """
    lines = []

    def failed(name):
        value = results.get(name)
        return isinstance(value, Exception) or isinstance(value, str)

    for name in PRECHECK_COMMANDS:
        if name not in results:
            continue
        if failed(name):
            lines.append("- %s: could not run (%s)" % (name, results[name]))

    validate = results.get("validate")
    if isinstance(validate, dict):
        lines.append("- validate: %s%s" % (
            "OK" if validate.get("ok") else "FAILED",
            "" if validate.get("ok") else " — %s" % validate.get("errors")))

    solid = results.get("check_solid")
    if isinstance(solid, dict):
        parts = ["%s: %d solid(s), valid=%s, closed=%s" % (
            obj.get("name"), obj.get("solid_count", 0), obj.get("is_valid"), obj.get("is_closed"))
            for obj in solid.get("objects", [])]
        lines.append("- solids: %s" % ("; ".join(parts) if parts else "no visible solids"))

    digest = results.get("model_digest")
    if isinstance(digest, dict):
        box = digest.get("overall_bounding_box") or {}
        if box.get("min") and box.get("max"):
            size = [round(box["max"][i] - box["min"][i], 3) for i in range(3)]
            lines.append("- overall size (x,y,z): %s mm" % size)
        aliases = []
        for sheet, cells in (digest.get("spreadsheets") or {}).items():
            for cell, info in (cells or {}).items():
                if info.get("alias") is not None:
                    value = info.get("value")
                    if isinstance(value, dict):
                        value = value.get("value")
                    aliases.append("%s=%s" % (info["alias"], value))
        if aliases:
            lines.append("- parameters: %s" % ", ".join(sorted(aliases)))

    probe = results.get("feature_probe")
    if isinstance(probe, dict):
        for obj in probe.get("objects", []):
            if obj.get("error"):
                continue
            plane = obj.get("largest_plane") or {}
            if plane:
                lines.append("- %s: largest flat face normal %s, body stands off it by %s mm"
                             % (obj.get("object"),
                                [round(value, 3) for value in plane.get("normal", [])],
                                round(plane.get("standoff_mm") or 0.0, 3)))
            holes = [hole for hole in obj.get("holes", []) if hole.get("kind") == "hole"]
            if holes:
                lines.append("  bores: %s" % "; ".join(
                    "d=%.3g mm along %s (sweep %.0f deg)"
                    % (hole.get("diameter_mm", 0),
                       [round(value, 3) for value in hole.get("axis", [])],
                       hole.get("angle_deg", 360)) for hole in holes))

    printability = results.get("print_readiness")
    if isinstance(printability, dict):
        for obj in printability.get("objects", []):
            if obj.get("error"):
                continue
            lines.append("- %s: overhang area %.3g%%, thinnest bbox dimension %.3g mm"
                         % (obj.get("object"), obj.get("overhang_area_pct", 0.0),
                            obj.get("min_bbox_dim", 0.0)))

    if not lines:
        return "(no deterministic checks could be run)"
    return "\n".join(lines)
