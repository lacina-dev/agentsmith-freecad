#!/usr/bin/env python3
"""snapshot_from_history.py -- build a comparable snapshot out of e2e-history.jsonl.

`run_e2e.py --json` emits a snapshot directly, but only for the tasks in that one
invocation. Measuring a variant usually means running several single-task
invocations back to back (so a crash costs one task, not the set), and those land
in the history file instead. This turns a slice of that history into the same
shape `compare.py` reads.

Selection is explicit on purpose — by model, by task, and by whether MCP or the
review loop were on. Two runs that differ only in a flag look identical otherwise,
and silently mixing them would produce a comparison that means nothing.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HISTORY = os.path.join(HERE, "results", "e2e-history.jsonl")


def load_history(path):
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def select(rows, tasks=None, model=None, mcp=None, review=None, since=None):
    """Newest matching run per task. Later runs supersede earlier ones."""
    chosen = {}
    for row in rows:
        if tasks and row.get("task_id") not in tasks:
            continue
        if model and row.get("model") != model:
            continue
        if mcp is not None and bool(row.get("mcp")) != mcp:
            continue
        if review is not None and ("grade_before_review" in row) != review:
            continue
        if since and (row.get("timestamp_iso") or "") < since:
            continue
        chosen[row["task_id"]] = row       # history is append-only, so last wins
    return [chosen[key] for key in sorted(chosen)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--tasks", help="comma-separated task ids to include")
    parser.add_argument("--model")
    parser.add_argument("--mcp", dest="mcp", action="store_true", default=None,
                        help="only runs with MCP enabled")
    parser.add_argument("--no-mcp", dest="mcp", action="store_false",
                        help="only runs without MCP")
    parser.add_argument("--review", dest="review", action="store_true", default=None,
                        help="only runs that went through the review loop")
    parser.add_argument("--since", help="ISO timestamp lower bound")
    parser.add_argument("--label", default="", help="stage label stored in the snapshot")
    parser.add_argument("-o", "--out", help="write here instead of stdout")
    opts = parser.parse_args(argv)

    rows = load_history(opts.history)
    tasks = [t.strip() for t in opts.tasks.split(",")] if opts.tasks else None
    picked = select(rows, tasks, opts.model, opts.mcp, opts.review, opts.since)
    if not picked:
        print("No runs matched.", file=sys.stderr)
        return 1

    snapshot = {
        "label": opts.label,
        "model": opts.model or (picked[0].get("model") if picked else None),
        "mcp": opts.mcp,
        "review": opts.review,
        "tasks": len(picked),
        "records": picked,
    }
    text = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    if opts.out:
        with open(opts.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print("wrote %s (%d tasks)" % (opts.out, len(picked)), file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
