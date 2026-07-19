#!/usr/bin/env python3
"""End-to-end task runner for the AgentSmith FreeCAD modeling system.

This is Layer 2 on top of run_eval.py. Where run_eval.py only *grades* whatever
model is already open, run_e2e.py drives a full task: it prepares a fresh
sandbox document, launches a real backend CLI (codex / claude) to model the
task through the bridge, then grades the result by reusing run_eval.py's check
logic against the same golden-task JSON.

    ┌─ run_e2e.py (this file) ──────────────────────────────────────────┐
    │  per task:                                                        │
    │    1. create + save a sandbox document  (bridge execute_python)   │
    │    2. build the worker prompt (harness + contract + task)         │
    │    3. launch backend CLI as a subprocess, hard timeout            │
    │    4. grade the active doc via run_eval.grade_task(task)          │
    │    5. close the sandbox document                                  │
    └───────────────────────────────────────────────────────────────────┘

WHAT THIS DOES that run_eval.py does not: it launches a REAL AI backend session
(codex/claude) that costs real tokens and drives your live FreeCAD via the
bridge. It is NOT offline. See eval/README.md for the full precondition list.

Deliberately DROPPED vs. the production panel (BridgeGui._launch...) — this is a
bare eval sandbox, not the supervised chat panel:
  * no watchdog / SIGTERM budget enforcement (we use a plain subprocess timeout)
  * no checkpoints / undo transaction bracketing around the task
  * no reference images, no "before" visual context / screenshots
  * no persisted per-document history, no reviewer/autofix second pass
  * no protected-document guard (we operate only on docs we created in a sandbox)

Usage:
    python3 eval/run_e2e.py --all --backend codex
    python3 eval/run_e2e.py --task eval/tasks/snap_fit_box.json --backend claude --model opus
    python3 eval/run_e2e.py --all --backend codex --json
    python3 eval/run_e2e.py --all --discovery /nonexistent      # -> clean exit 2

Exit codes:
    0  every task's every graded check passed
    1  at least one check failed / errored (or a backend run failed)
    2  preconditions not met (bridge unreachable, wrong access level, no active
       document, or unsafe foreign documents open) — nothing was run
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

# run_eval.py lives next to this file; import it as a module and reuse both its
# bridge transport (bridge_call / BridgeError / BridgeCallError) and its grading
# logic (grade_task + the individual check_* functions). No fork, no refactor:
# we only set its module-level DISCOVERY_FILE to point bridge_call at the right
# discovery file, exactly as run_eval's own --discovery flag does.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_eval  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "eval")
TASKS_DIR = os.path.join(EVAL_DIR, "tasks")
RESULTS_DIR = os.path.join(EVAL_DIR, "results")
DEFAULT_WORKDIR = os.path.join(RESULTS_DIR, "e2e-workdir")
LOGS_DIR = os.path.join(RESULTS_DIR, "e2e-logs")
HISTORY_FILE = os.path.join(RESULTS_DIR, "e2e-history.jsonl")

BRIDGE_CLIENT = os.path.join(REPO_ROOT, "freecad_bridge_client.py")
HARNESS_DIR = os.path.join(REPO_ROOT, "freecad_bridge", "harness")
DEFAULT_DISCOVERY = run_eval.DISCOVERY_FILE

# Backend CLI program names, mirrored from BridgeGui._backend_launch.
BACKEND_COMMANDS = {"codex": "codex", "claude": "claude"}

# --------------------------------------------------------------------------
# Auditable execute_python snippets.
# Every bit of Python we push over the bridge is spelled out here as a constant
# so a reviewer can read exactly what runs inside FreeCAD. Each one operates ONLY
# on the sandbox document named in {name!r}; none of them ever touches another
# document. {name!r} / {path!r} embed as safe Python string literals via repr.
# --------------------------------------------------------------------------

# Create a brand-new document and make it active. FreeCAD auto-suffixes the
# internal Name if `name` is taken, so we return the real Name and only ever act
# on that afterwards. (The bridge requires an already-open active document to
# attach its transaction to — that is a documented precondition, see main().)
CREATE_DOC_CODE = """\
import FreeCAD as App
name = {name!r}
doc = App.newDocument(name)
try:
    App.setActiveDocument(doc.Name)
except Exception:
    pass
result = {{"name": doc.Name, "label": doc.Label}}
"""

# Re-assert that our sandbox document is the active one, right before grading,
# so run_eval's checks (which target the active document) grade the right model.
SET_ACTIVE_CODE = """\
import FreeCAD as App
name = {name!r}
if name in App.listDocuments():
    App.setActiveDocument(name)
    result = {{"active": name}}
else:
    result = {{"active": None, "missing": name}}
"""

# Close ONLY the sandbox document we created (matched by its exact internal
# Name). If it is gone already this is a no-op. Note: the bridge's supervised-file
# guard blocks App.closeDocument when protected_documents is non-empty; in a
# clean eval sandbox nothing is protected, but we still handle the refusal.
CLOSE_DOC_CODE = """\
import FreeCAD as App
name = {name!r}
if name in App.listDocuments():
    App.closeDocument(name)
    result = {{"closed": name}}
else:
    result = {{"closed": None, "missing": name}}
"""


# --------------------------------------------------------------------------
# Bridge helpers (all transport reused from run_eval)
# --------------------------------------------------------------------------

def bridge(command, args=None, timeout=30):
    """Thin wrapper over run_eval.bridge_call so this module reads clearly."""
    return run_eval.bridge_call(command, args, timeout=timeout)


def execute_python(code, timeout=60):
    return bridge("execute_python", {"code": code}, timeout=timeout)


# --------------------------------------------------------------------------
# Worker prompt assembly (simplified mirror of BridgeGui._harness_text + context)
# --------------------------------------------------------------------------

def assemble_harness_text():
    """Concatenate the modeling harness the same way BridgeGui._harness_text does:
    always_include files first, then a generated index of every playbook with its
    trigger, then every playbook body. Read-only reads of freecad_bridge/harness/."""
    registry = {"always_include": [], "playbooks": []}
    try:
        with open(os.path.join(HARNESS_DIR, "registry.json"), "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            registry["always_include"] = list(loaded.get("always_include", []))
            registry["playbooks"] = [p for p in loaded.get("playbooks", []) if p.get("id") and p.get("file")]
    except Exception as exc:
        print("WARNING: could not read harness registry: %s" % exc, file=sys.stderr)

    def _read(name):
        try:
            with open(os.path.join(HARNESS_DIR, name), "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except Exception as exc:
            print("WARNING: harness file %s: %s" % (name, exc), file=sys.stderr)
            return ""

    playbooks = registry["playbooks"]
    sections = [_read(name) for name in registry["always_include"]]
    if playbooks:
        index = [
            "## Harness library — apply what fits",
            "All playbooks below are ALWAYS provided; there is no single mode. Apply every "
            "playbook whose trigger matches the part's character or how it will be used, and "
            "ignore the ones that don't apply.",
        ]
        for playbook in playbooks:
            trigger = playbook.get("trigger")
            label = playbook.get("label", playbook["id"])
            index.append("- **%s** — apply when %s" % (label, trigger) if trigger else "- **%s**" % label)
        sections.append("\n".join(index))
        sections.extend(_read(playbook["file"]) for playbook in playbooks)

    label = "full harness (%d playbooks)" % len(playbooks)
    return label, "\n\n".join(section for section in sections if section)


def build_worker_prompt(task, doc_name, fcstd_path, discovery_file):
    """Compose the backend worker prompt: harness + a compact delivery contract +
    the task prompt. A deliberately simplified version of the panel's context
    (see the module docstring for what was dropped)."""
    kind_label, harness_text = assemble_harness_text()
    prompt = task.get("prompt", "")
    task_id = task.get("id", "task")
    return (
        "You are an automated FreeCAD modeling worker running inside an EVALUATION "
        "sandbox (no human in the loop). Model the task below by editing the ALREADY "
        "OPEN, ALREADY ACTIVE document through the AgentSmith bridge, then validate "
        "and save it.\n\n"
        "Task id: %s\n"
        "Project (your CWD): %s\n"
        "Live active document: %s   (saved at %s)\n\n"
        "MODELING HARNESS (%s) — always-on methodology; apply each playbook whose "
        "trigger matches the part:\n%s\n\n"
        "BRIDGE CONTRACT:\n"
        "- Bridge client: %s\n"
        "- Discovery file: %s\n"
        "- Drive the model like this: `python3 %s <command> '<json-args>'`.\n"
        "  Start with `ping`, `capabilities`, `document_info`, and `model_digest`.\n\n"
        "MANDATE:\n"
        "1. Modify the ACTIVE document '%s' via the bridge (prefer structured commands; "
        "use `execute_python` only when necessary). Do NOT create, open, close, rename, "
        "move or saveAs any document — the sandbox document already exists and is active; "
        "just build inside it.\n"
        "2. Keep the model a robust, editable parametric solid: drive dimensions from a "
        "Parameters spreadsheet as the task requires.\n"
        "3. When done: run bridge `validate` (fix anything it flags), then bridge `save` "
        "(no path argument — save in place). Do not saveAs or change the file path.\n"
        "4. If you genuinely cannot reach or edit the live document, stop and say FAILED; "
        "never claim success from source-file edits alone.\n\n"
        "USER REQUEST: %s"
        % (
            task_id, REPO_ROOT, doc_name, fcstd_path,
            kind_label, harness_text or "(no harness playbook loaded)",
            BRIDGE_CLIENT, discovery_file, BRIDGE_CLIENT,
            doc_name, prompt,
        )
    )


# --------------------------------------------------------------------------
# Backend launch (flag shapes mirrored from BridgeGui._backend_launch)
# --------------------------------------------------------------------------

def build_backend_command(backend, model, context):
    program = BACKEND_COMMANDS[backend]
    if backend == "codex":
        args = ["exec", "--json", "--skip-git-repo-check", "-C", REPO_ROOT,
                "-s", "danger-full-access", context]
        if model:
            args[1:1] = ["-m", model]
    elif backend == "claude":
        args = ["-p", "--output-format", "stream-json", "--verbose",
                "--dangerously-skip-permissions", "--no-session-persistence", context]
        if model:
            args[1:1] = ["--model", model]
    else:
        raise ValueError("Unknown backend: %s" % backend)
    return program, args


def launch_backend(backend, model, context, log_path, task_timeout):
    """Run the backend CLI as a subprocess with a HARD wall-clock timeout, streaming
    combined stdout/stderr to log_path. On timeout the whole process group is killed.
    Returns (exit_code, timed_out, wall_seconds)."""
    program, args = build_backend_command(backend, model, context)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    start = time.monotonic()
    timed_out = False
    with open(log_path, "w", encoding="utf-8") as log:
        log.write("# backend=%s model=%s\n# cmd=%s %s\n# started=%s\n\n" % (
            backend, model or "<default>", program,
            " ".join(a if a is not context else "<CONTEXT>" for a in args),
            datetime.now(timezone.utc).isoformat()))
        log.flush()
        try:
            proc = subprocess.Popen(
                [program] + args, cwd=REPO_ROOT,
                stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, text=True,
            )
        except FileNotFoundError:
            log.write("\n# ERROR: backend command %r not found on PATH\n" % program)
            return 127, False, time.monotonic() - start
        try:
            exit_code = proc.wait(timeout=task_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_group(proc)
            exit_code = proc.returncode if proc.returncode is not None else -signal.SIGTERM
            log.write("\n# TIMEOUT after %ss — process group terminated\n" % task_timeout)
    return exit_code, timed_out, time.monotonic() - start


def _terminate_group(proc):
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
            proc.wait(timeout=10)
        except Exception:
            pass
    except ProcessLookupError:
        pass


# --------------------------------------------------------------------------
# Sandbox document lifecycle
# --------------------------------------------------------------------------

def create_sandbox_doc(task_id, workdir):
    """Create + save a fresh sandbox document e2e_<task_id>.FCStd in workdir.
    Returns (doc_name, fcstd_path)."""
    os.makedirs(workdir, exist_ok=True)
    base = "e2e_%s" % task_id
    fcstd_path = os.path.join(workdir, base + ".FCStd")
    created = execute_python(CREATE_DOC_CODE.format(name=base))
    doc_name = (created or {}).get("name", base)
    # Save via the dedicated bridge `save` command (not through execute_python, so
    # it is unaffected by the guard that blocks `.saveAs(` in execute_python code).
    bridge("save", {"document": doc_name, "path": fcstd_path})
    return doc_name, fcstd_path


def ensure_active(doc_name):
    execute_python(SET_ACTIVE_CODE.format(name=doc_name))


def close_sandbox_doc(doc_name):
    try:
        execute_python(CLOSE_DOC_CODE.format(name=doc_name))
    except run_eval.BridgeCallError as exc:
        print("WARNING: could not close sandbox document %r (%s) — leaving it open."
              % (doc_name, exc), file=sys.stderr)


# --------------------------------------------------------------------------
# Preconditions & safety rails
# --------------------------------------------------------------------------

class PreconditionError(RuntimeError):
    pass


def check_preconditions(force):
    """Verify a live FreeCAD + AgentSmith bridge in Edit+Python, an active document
    to attach to, and no unsafe foreign documents. Raises PreconditionError (→ exit 2)
    with actionable guidance; never leaks a traceback."""
    try:
        ping = bridge("ping")
    except run_eval.BridgeError as exc:
        raise PreconditionError(
            "FreeCAD bridge unreachable — nothing was run.\n\n%s\n\n"
            "run_e2e.py needs a LIVE FreeCAD with the AgentSmith bridge panel running "
            "in Edit + Python-enabled mode." % exc)
    except run_eval.BridgeCallError as exc:
        raise PreconditionError("Bridge responded with an error to 'ping': %s" % exc)

    if ping.get("access_level") != "edit":
        raise PreconditionError(
            "Bridge access level is %r, but run_e2e.py needs 'edit' (it creates, "
            "saves and edits documents). Set the Bridge panel access to Edit + Python."
            % ping.get("access_level"))
    if not ping.get("python_enabled"):
        raise PreconditionError(
            "Bridge Python execution is disabled, but run_e2e.py needs it to create the "
            "sandbox document. Enable Python in the Bridge panel (Edit + Python).")

    # The bridge's execute_python is transactional and attaches to the ACTIVE
    # document, so at least one document must already be open.
    try:
        docs = bridge("documents")
    except run_eval.BridgeCallError as exc:
        raise PreconditionError("Could not list open documents: %s" % exc)
    if not docs.get("active"):
        raise PreconditionError(
            "No active FreeCAD document. Open or create any document first "
            "(File > New is enough) — the bridge needs an active document to attach its "
            "transaction to while run_e2e.py creates its own sandbox document. Your "
            "document is never touched.")

    # Safety rail: refuse if any open document is modified and NOT one of ours,
    # so a crash can never cost the user unsaved work. --force overrides.
    risky = []
    for name in docs.get("documents", []):
        try:
            info = bridge("document_info", {"document": name})
        except run_eval.BridgeCallError:
            continue
        if info.get("modified") and not name.startswith("e2e_"):
            risky.append((name, info.get("file_name") or "<unsaved>"))
    if risky and not force:
        listing = "\n".join("  - %s (%s)" % (n, f) for n, f in risky)
        raise PreconditionError(
            "Refusing to start: these open documents have unsaved changes and are not "
            "eval sandbox documents:\n%s\n\n"
            "Save or close them first, or pass --force to run anyway (run_e2e.py only "
            "ever edits documents it creates in the sandbox, but this guards your work "
            "against an unexpected failure)." % listing)
    return ping


# --------------------------------------------------------------------------
# Per-task orchestration
# --------------------------------------------------------------------------

def run_task(task, task_path, args):
    """Full e2e flow for one task. Returns a report dict."""
    task_id = task.get("id") or os.path.splitext(os.path.basename(task_path))[0]
    log_path = os.path.join(LOGS_DIR, "%s.log" % task_id)
    record = {
        "task_id": task_id,
        "title": task.get("title", task_id),
        "backend": args.backend,
        "model": args.model,
        "log_path": log_path,
        "doc_name": None,
        "fcstd_path": None,
        "backend_exit_code": None,
        "timed_out": False,
        "wall_time_sec": None,
        "grade": None,
        "error": None,
    }
    start = time.monotonic()
    doc_name = None
    try:
        doc_name, fcstd_path = create_sandbox_doc(task_id, args.workdir)
        record["doc_name"] = doc_name
        record["fcstd_path"] = fcstd_path

        context = build_worker_prompt(task, doc_name, fcstd_path, args.discovery)
        exit_code, timed_out, wall = launch_backend(
            args.backend, args.model, context, log_path, args.task_timeout)
        record["backend_exit_code"] = exit_code
        record["timed_out"] = timed_out

        # Grade the active document by reusing run_eval's check logic verbatim.
        ensure_active(doc_name)
        record["grade"] = run_eval.grade_task(task)
    except run_eval.BridgeError as exc:
        # Bridge dropped mid-run: fatal, propagate so the whole run aborts.
        record["error"] = "bridge lost mid-task: %s" % exc
        raise
    except run_eval.BridgeCallError as exc:
        record["error"] = "bridge command failed: %s" % exc
    finally:
        record["wall_time_sec"] = round(time.monotonic() - start, 2)
        if doc_name:
            close_sandbox_doc(doc_name)
    return record


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def append_history(record):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    grade = record.get("grade") or {}
    entry = {
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "task_id": record["task_id"],
        "backend": record["backend"],
        "model": record["model"],
        "score": "%d/%d" % (grade.get("passed_count", 0), grade.get("graded_count", 0)) if grade else None,
        "wall_time_sec": record["wall_time_sec"],
        "backend_exit_code": record["backend_exit_code"],
        "timed_out": record["timed_out"],
        "all_passed": bool(grade.get("all_passed")) if grade else False,
    }
    with open(HISTORY_FILE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def format_scoreboard(records):
    lines = ["# End-to-end scoreboard", ""]
    lines.append("| Task | Backend | Model | Score | Exit | Timeout | Wall (s) | Log |")
    lines.append("|---|---|---|---|---|---|---|---|")
    total_pass = total_graded = 0
    for rec in records:
        grade = rec.get("grade")
        if grade:
            score = "%d/%d" % (grade["passed_count"], grade["graded_count"])
            total_pass += grade["passed_count"]
            total_graded += grade["graded_count"]
        else:
            score = "—"
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            rec["task_id"], rec["backend"], rec["model"] or "<default>", score,
            rec["backend_exit_code"], "yes" if rec["timed_out"] else "no",
            rec["wall_time_sec"], os.path.relpath(rec["log_path"], REPO_ROOT)))
    lines.append("")
    lines.append("**Overall: %d/%d checks passed across %d task(s)**"
                 % (total_pass, total_graded, len(records)))
    for rec in records:
        if rec.get("error"):
            lines.append("")
            lines.append("> %s: %s" % (rec["task_id"], rec["error"]))
        if rec.get("grade"):
            lines.append("")
            lines.append(run_eval.format_markdown(rec["grade"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="run_e2e.py",
        description=(
            "Run full end-to-end AgentSmith tasks: prepare a sandbox document, launch a "
            "backend CLI to model each golden task through the FreeCAD bridge, then grade "
            "the result with run_eval.py's check logic. Requires a LIVE FreeCAD + bridge "
            "in Edit+Python and drives a REAL backend session (costs tokens)."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--all", action="store_true",
                          help="Run every eval/tasks/*.json (default if --task is omitted)")
    selector.add_argument("--task", metavar="PATH", help="Run a single task JSON file")
    parser.add_argument("--backend", choices=sorted(BACKEND_COMMANDS), default="codex",
                        help="Backend CLI to launch (default: codex)")
    parser.add_argument("--model", default=None,
                        help="Backend model id (e.g. opus, sonnet, or a codex model slug). "
                             "Default: the backend's own default.")
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR, metavar="DIR",
                        help="Sandbox directory for eval .FCStd documents (default: %(default)s)")
    parser.add_argument("--task-timeout", type=int, default=900, metavar="SECONDS",
                        help="Hard wall-clock limit per backend task run (default: 900)")
    parser.add_argument("--discovery", default=DEFAULT_DISCOVERY, metavar="PATH",
                        help="Bridge discovery JSON file (default: %(default)s)")
    parser.add_argument("--force", action="store_true",
                        help="Override the safety rail that refuses to run when non-sandbox "
                             "documents have unsaved changes.")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON instead of the Markdown scoreboard")
    parser.add_argument("--no-save", action="store_true",
                        help="Do not append to eval/results/e2e-history.jsonl")
    args = parser.parse_args(argv)

    # Point run_eval's transport (and our own bridge helpers) at the chosen discovery file.
    run_eval.DISCOVERY_FILE = args.discovery
    args.workdir = os.path.abspath(args.workdir)

    # Resolve task list.
    if args.task:
        task_paths = [args.task]
    else:
        import glob
        task_paths = sorted(glob.glob(os.path.join(TASKS_DIR, "*.json")))
        if not task_paths:
            print("No task files found in %s" % TASKS_DIR, file=sys.stderr)
            return 2

    # Preconditions — clean exit 2, no traceback, before touching anything.
    try:
        check_preconditions(args.force)
    except PreconditionError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    records = []
    try:
        for path in task_paths:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    task = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                print("ERROR: could not load task %s: %s" % (path, exc), file=sys.stderr)
                records.append({
                    "task_id": os.path.splitext(os.path.basename(path))[0],
                    "title": path, "backend": args.backend, "model": args.model,
                    "log_path": "", "backend_exit_code": None, "timed_out": False,
                    "wall_time_sec": None, "grade": None,
                    "error": "could not load task: %s" % exc,
                })
                continue
            records.append(run_task(task, path, args))
    except run_eval.BridgeError as exc:
        print("ERROR: bridge lost mid-run — aborting. %s" % exc, file=sys.stderr)
        if not args.no_save:
            for rec in records:
                append_history(rec)
        return 2

    if not args.no_save:
        for rec in records:
            append_history(rec)

    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
    else:
        print(format_scoreboard(records))

    all_ok = bool(records) and all(
        rec.get("grade") and rec["grade"].get("all_passed") for rec in records)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
