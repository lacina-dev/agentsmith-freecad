#!/usr/bin/env python3
"""compare.py -- diff two eval snapshots so a change can be judged by numbers.

Takes two JSON files produced by `run_e2e.py --json` (or `run_eval.py --all --json`)
and prints a per-task, per-check table of what moved. Use it to answer the only
question that matters after a harness change: did this actually help?

Repeated runs (`run_e2e.py --repeat N`) are aggregated into a pass rate per check,
because a single run of a stochastic backend is noise: 2/3 and 3/3 are different
facts, and a lone pass/fail cannot tell them apart.

Usage:
    python3 eval/compare.py BEFORE.json AFTER.json
    python3 eval/compare.py BEFORE.json AFTER.json --labels "opus-5,gpt-5.6-sol"
    python3 eval/compare.py BEFORE.json AFTER.json --json

Exit codes:
    0  after is no worse than before (no check regressed)
    1  at least one check regressed
    2  the inputs could not be read or contain no comparable tasks

Stdlib only. Accepts either shape: a run_e2e record list (each with a nested
"grade"), a run_eval report list, or a single report object.
"""

import argparse
import json
import os
import sys


def load_snapshot(path):
    """Read one snapshot into {task_id: {"runs": [grade, ...], "meta": {...}}}.

    Tolerates the three shapes the eval scripts emit, and collects repeated runs
    of the same task instead of letting the last one win.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print("ERROR: cannot read %s: %s" % (path, exc), file=sys.stderr)
        raise SystemExit(2)

    return load_snapshot_from_records(data if isinstance(data, list) else [data])


def load_snapshot_from_records(records):
    """The parsing half of load_snapshot, split out so it can be tested directly."""
    snapshot = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        # run_e2e wraps run_eval's report in "grade" and adds run metadata.
        grade = record.get("grade") if "grade" in record else record
        if not isinstance(grade, dict):
            continue
        task_id = grade.get("task_id") or record.get("task_id")
        if not task_id:
            continue
        entry = snapshot.setdefault(task_id, {"runs": [], "meta": {}})
        entry["runs"].append(grade)
        for key in ("backend", "model"):
            if record.get(key) is not None:
                entry["meta"][key] = record[key]
        # Wall time and exit codes vary per run; keep them as lists so a slow or
        # crashed run stays visible instead of being averaged away.
        for key in ("wall_time_sec", "backend_exit_code", "timed_out"):
            if record.get(key) is not None:
                entry["meta"].setdefault(key + "_runs", []).append(record[key])
    return snapshot


def _check_rates(runs):
    """{check_name: (passes, graded_runs)} across every run of one task.

    A run where the check was skipped (unsupported bridge command) does not count
    toward either number — same convention the scorecard uses.
    """
    rates = {}
    for grade in runs:
        for result in (grade or {}).get("results", []):
            name = result.get("check") or result.get("name")
            if not name:
                continue
            passes, graded = rates.get(name, (0, 0))
            if result.get("status") == "skipped":
                # Keep the check visible in the table, but let it move neither
                # numerator nor denominator.
                rates[name] = (passes, graded)
                continue
            rates[name] = (passes + (1 if result.get("status") == "pass" else 0), graded + 1)
    return rates


def _rate(pair):
    passes, graded = pair
    return (passes / graded) if graded else None


def compare(before, after):
    """Build the comparison structure for every task present in either snapshot."""
    tasks = []
    for task_id in sorted(set(before) | set(after)):
        before_entry = before.get(task_id) or {"runs": [], "meta": {}}
        after_entry = after.get(task_id) or {"runs": [], "meta": {}}
        before_rates = _check_rates(before_entry["runs"])
        after_rates = _check_rates(after_entry["runs"])

        checks = []
        for name in sorted(set(before_rates) | set(after_rates)):
            was = before_rates.get(name)
            now = after_rates.get(name)
            rate_was, rate_now = _rate(was) if was else None, _rate(now) if now else None
            if was is None or now is None:
                delta = "added" if was is None else "removed"
            elif rate_was is None or rate_now is None:
                delta = "skipped"
            elif rate_now > rate_was:
                delta = "fixed"
            elif rate_now < rate_was:
                delta = "regressed"
            else:
                delta = "same"
            checks.append({"check": name, "before": was, "after": now, "delta": delta})

        def _summary(entry):
            runs = entry["runs"]
            return {
                "runs": len(runs),
                "fully_passed": sum(1 for grade in runs if grade.get("all_passed")),
                "passed": sum(grade.get("passed_count", 0) for grade in runs),
                "graded": sum(grade.get("graded_count", 0) for grade in runs),
                **entry["meta"],
            }

        tasks.append({
            "task_id": task_id,
            "before": _summary(before_entry),
            "after": _summary(after_entry),
            "checks": checks,
            "regressed": [c["check"] for c in checks if c["delta"] == "regressed"],
            "fixed": [c["check"] for c in checks if c["delta"] == "fixed"],
        })
    return tasks


def _cell(pair):
    if not pair:
        return "—"
    passes, graded = pair
    if not graded:
        return "⊘"
    if graded == 1:
        return "✓" if passes else "✗"
    return "%d/%d" % (passes, graded)


def format_markdown(tasks, labels):
    before_label, after_label = labels
    lines = ["# Eval comparison", "",
             "**A:** %s   **B:** %s" % (before_label, after_label), ""]

    for task in tasks:
        lines.append("## %s" % task["task_id"])
        lines.append("")
        lines.append("| | runs | fully passed | checks | model | wall (s) |")
        lines.append("|---|---|---|---|---|---|")
        for name, side in (("A", task["before"]), ("B", task["after"])):
            walls = side.get("wall_time_sec_runs") or []
            lines.append("| %s | %d | %d/%d | %d/%d | %s | %s |" % (
                name, side.get("runs", 0),
                side.get("fully_passed", 0), side.get("runs", 0),
                side.get("passed", 0), side.get("graded", 0),
                side.get("model") or "—",
                ", ".join("%.0f" % w for w in walls) if walls else "—"))
        lines.append("")
        lines.append("| check | A | B | |")
        lines.append("|---|:--:|:--:|---|")
        for check in task["checks"]:
            marker = {"regressed": "**regression**", "fixed": "improved",
                      "added": "new", "removed": "gone",
                      "skipped": "skipped", "same": ""}[check["delta"]]
            lines.append("| %s | %s | %s | %s |" % (
                check["check"], _cell(check["before"]), _cell(check["after"]), marker))
        lines.append("")

    regressed = sum(len(task["regressed"]) for task in tasks)
    fixed = sum(len(task["fixed"]) for task in tasks)
    lines.append("**Summary:** %d improved, %d regressed across %d task(s)."
                 % (fixed, regressed, len(tasks)))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Diff two eval snapshots (run_e2e.py --json / run_eval.py --all --json).")
    parser.add_argument("before", help="Baseline snapshot JSON")
    parser.add_argument("after", help="Snapshot to compare against the baseline")
    parser.add_argument("--labels", default=None,
                        help="Comma-separated display labels, e.g. 'baseline,new-harness'")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit the comparison as JSON instead of Markdown")
    args = parser.parse_args(argv)

    before = load_snapshot(args.before)
    after = load_snapshot(args.after)
    tasks = compare(before, after)
    if not tasks:
        print("ERROR: no comparable tasks in the two snapshots", file=sys.stderr)
        return 2

    if args.labels:
        labels = [part.strip() for part in args.labels.split(",")]
        if len(labels) != 2:
            parser.error("--labels needs exactly two comma-separated values")
    else:
        labels = [os.path.basename(args.before), os.path.basename(args.after)]

    if args.as_json:
        print(json.dumps({"a": labels[0], "b": labels[1], "tasks": tasks},
                         indent=2, ensure_ascii=False))
    else:
        print(format_markdown(tasks, labels))

    return 1 if any(task["regressed"] for task in tasks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
