# eval — regression scoring for the FreeCAD modeling agent

This directory has **two** runners:

- **`run_eval.py`** — an *offline grader*. It scores whatever model is CURRENTLY
  OPEN in FreeCAD against a golden task; it never launches an AI backend. Most of
  this README is about it.
- **`run_e2e.py`** — a *full end-to-end runner* (Layer 2). For each golden task it
  prepares a fresh sandbox document, launches a **real** backend CLI (codex /
  claude) to model it through the bridge, then grades the result by reusing
  `run_eval.py`'s check logic. It drives a live backend session and **costs real
  tokens** — see the [run_e2e.py section](#end-to-end-runner-run_e2epy) below.

## What run_eval.py is

`run_eval.py` is a **grader**, not a test runner for the AI backend. It does not
launch Codex/Claude/Copilot and does not build anything. It answers one
question: *"does the FreeCAD model that is CURRENTLY OPEN in the GUI satisfy
this golden task's objective checks?"*

It talks only to the FreeCAD Codex Bridge (the same socket protocol used by
`freecad_bridge_client.py` — see that file and `freecad_bridge/harness/core.md`
for the underlying conventions), using read-only-ish commands: `ping`,
`model_digest`, `validate`, `check_solid`, `print_readiness` (if the bridge
supports it — see the `print_readiness` check below for the skipped-on-old-bridge
fallback), and — only for the parametric probe — `spreadsheet_set` +
`recompute` (which it always restores afterwards).

The intended workflow is a **before/after regression check**: run the same
task through the modeling agent under harness version A, grade the result,
then repeat under harness version B, and diff the scorecards. Because the
grader is deterministic and offline, it isolates "did the harness change
actually make outputs better" from the noise of re-reading a live LLM
transcript.

## Important caveat

**The grader only looks at whatever document is open in FreeCAD right now.**
It does not know or care how that model got there. The workflow is always:

1. Open FreeCAD (with the Bridge panel active) and load/build the model for a
   given task — by hand, via the modeling agent, or by opening a saved
   `.FCStd`.
2. Run `python3 eval/run_eval.py eval/tasks/<task>.json` (or `--all`).
3. Read the scorecard.

Grading a random unrelated model against a task is expected to fail most
checks cleanly — the grader still must run without crashing; a low score
there is not a bug in the grader.

## Running it

```bash
# Grade one task against the currently open model
python3 eval/run_eval.py eval/tasks/m6_hex_nut.json

# Grade every task in eval/tasks/
python3 eval/run_eval.py --all

# Machine-readable output (a JSON object for a single task, or a JSON array for --all)
python3 eval/run_eval.py --all --json

# Skip appending to eval/results/history.jsonl for this run
python3 eval/run_eval.py --all --no-save

# Point at a different bridge discovery file (mainly for testing/tooling)
python3 eval/run_eval.py eval/tasks/m6_hex_nut.json --discovery /path/to/other-bridge.json
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | every check in the run passed |
| `1` | at least one check failed or errored |
| `2` | the bridge could not be reached at all (nothing was graded) — the message tells you to open FreeCAD with the Bridge panel and a model loaded |

If `/tmp/freecad-agentsmith-bridge.json` is missing, unreadable, or the socket
connection is refused, `run_eval.py` prints clear guidance and exits `2`
instead of throwing a traceback.

## Task JSON schema

Each file in `eval/tasks/*.json` is one golden task:

```json
{
  "id": "m6_hex_nut",
  "title": "M6 hex nut",
  "prompt": "the natural-language request a user would type",
  "checks": {
    "validate": true,
    "watertight": true,
    "single_solid": true,
    "dimensions": [
      {"name": "overall_width", "source": "bbox", "axis": "x", "expected": 10.0, "tol": 0.2},
      {"name": "overall_width", "source": "bbox", "axis": "x", "object": "Pad", "expected": 10.0, "tol": 0.2},
      {"name": "WallThickness", "source": "alias", "alias": "WallThickness", "expected": 2.0, "tol": 0.01}
    ],
    "parametric_probe": {"alias": "OuterDia", "delta": 5.0},
    "print_readiness": {"up_axis": "z", "overhang_deg": 45.0, "max_overhang_area_pct": 10.0, "min_feature_mm": 1.0}
  }
}
```

All keys under `checks` are optional; the grader only runs the checks that
are present and skips the rest.

- **`validate`** (bool) — asserts the bridge's `validate` command returns
  `ok` equal to this value (normally `true`).
- **`watertight`** (bool) — calls `check_solid` (all visible objects) and
  requires every returned object to be `is_valid` and `is_closed`.
- **`single_solid`** (bool) — same `check_solid` call, requires every
  returned object to have exactly one solid (`solid_count == 1`), i.e. no
  stray shells/compounds. Kept separate from `watertight` so a multi-solid
  but individually-valid model, or a single solid with a topology defect,
  are distinguishable in the scorecard.
- **`solid_count`** (object `{"expected": N}`) — asserts the document contains
  **exactly N separate solid objects**, i.e. the number of *visible* objects
  whose shape holds at least one solid (`solid_count >= 1`) equals `N`. Reads
  from the same `check_solid` call as `watertight`/`single_solid`. This is the
  deliberate complement of `single_solid`: `single_solid` says "every object is
  exactly one solid"; `solid_count` says "there are exactly this many solid
  objects in the model". Objects that are pure sketches / wires / datums
  (`solid_count == 0`) are not counted. Use it for multi-part tasks — e.g. a
  two-part assembly modeled as two bodies side by side on the plate expects
  `{"expected": 2}` (see `snap_fit_box.json`).
- **`dimensions`** (list) — each entry is one numeric check:
  - `"source": "bbox"` — reads size along `axis` (`x`/`y`/`z`) from
    `model_digest`. With no `"object"`, it uses the whole model's
    `overall_bounding_box` (max − min over all *visible* objects); with
    `"object"` set (an object Name or Label), it uses that object's own
    `bounding_box.size` instead.
  - `"source": "alias"` — reads a Spreadsheet cell's value from
    `model_digest` by matching its `alias` (searches every Spreadsheet
    object in the document).
  - Every dimension entry needs `expected` and `tol` (absolute tolerance,
    same units as the value — normally mm).
  - **`mode`** (optional, default `"window"`) — some requirements are one-sided
    and a centred window states the wrong rule. Both alternatives take `expected`
    as the limit and ignore `tol`:
    - `"min"` — the measured value must be **at least** `expected`. Use it for
      "a seat **at least** 8 mm wide": a 30 mm seat satisfies the request, but a
      `expected 10, tol 3` window failed it (seen in the first real sweep).
    - `"max"` — the measured value must be **at most** `expected`. Use it for
      "sized slightly **undersize** for a press fit": a nominal 22.0 mm bore is a
      slip fit and does not meet the request, yet it sits inside a ±0.1 window
      around 21.9.

    A `min` floor is also the honest way to grade a dimension that shares the
    overall bounding box with something else — `snap_fit_box` measures two parts
    laid out side by side, so the exact X extent is a layout choice, not a
    dimension.
  - Values exactly at the tolerance pass: the comparison carries a 1e-9 epsilon,
    because `abs(22.0 - 21.9)` is `0.10000000000000142` in binary floating point
    and would otherwise fail a `tol: 0.1` check on a dimensionally fine model.
- **`parametric_probe`** (object, at most one per task) — proves the model
  is genuinely parametric, not hard-coded:
  1. Look up the named `alias` in `model_digest`, record its current value
     and which Spreadsheet/cell it lives in.
  2. `spreadsheet_set` it to `current + delta`, `recompute`.
  3. `validate` and require `ok == true`.
  4. **Always**, in a `finally` block, `spreadsheet_set` the alias back to
     its original value and `recompute` — even if step 3 failed or raised.
     If the restore itself fails, the grader prints a loud warning to
     stderr so you know the open model may be left mutated.
- **`print_readiness`** (object) — calls the bridge's `print_readiness`
  command (which reports, per visible object, overhang face area vs. a
  print-up axis/angle, and the smallest bounding-box dimension) and grades
  FDM printability:
  - `up_axis` (default `"z"`) and `overhang_deg` (default `45.0`) are passed
    straight through to the bridge command.
  - `max_overhang_area_pct` (optional) — the check fails if the *aggregate*
    overhang area percentage across all objects (total overhang area / total
    face area, summed across objects, not averaged per-object) exceeds this.
  - `min_feature_mm` (optional) — the check fails unless every graded
    object's `min_bbox_dim` is `>=` this value.
  - Objects the bridge reports as `{"object": ..., "error": ...}` are
    excluded from the aggregate and listed in the check's note; they are not
    by themselves a failure unless *no* object produced usable data (in
    which case the check reports `error`).
  - **Skipped-on-old-bridge semantics**: `print_readiness` is a newer bridge
    command. If the bridge responds with an "Unknown command" error (i.e.
    it predates this feature), the check reports status **`skipped`**
    instead of `error` or `fail` — shown as `⚠` in the scorecard, same
    symbol as `error`. A skipped check is **not** counted as a failure: it
    does not affect `all_passed` for the task, does not affect run_eval.py's
    process exit code, and is excluded from the score denominator (e.g.
    `Score: 7/7 checks passed (1 skipped)` rather than `7/8`). This lets you
    upgrade run_eval.py to grade printability without retroactively failing
    scorecards captured against an older bridge build.
- **`functional`** (object) — grades whether the part can physically do its
  job, not just whether it measures correctly. This exists because of a real
  failure: on 2026-07-18 a wall hook scored **7/7** on the dimensional checks
  while being an unusable flat plate (harness lessons L1/L2). Every sub-check
  present produces its own scorecard row, and each uses the bridge's
  `feature_probe` command (same skipped-on-old-bridge semantics as
  `print_readiness`).

  ```json
  "functional": {
    "mounting_axis": "auto",
    "hole_axis_tol_deg": 10.0,
    "min_hole_count": 2,
    "min_hole_dia_mm": 3.0,
    "max_hole_dia_mm": 8.0,
    "protrusion_mm": 30.0,
    "min_slab_ratio": 0.15
  }
  ```

  - **`mounting_axis`** — the axis normal to the mounting/contact surface.
    Default `"auto"`: the **largest planar face** across the graded objects is
    taken as that surface and its normal is used. Prefer `auto` — a golden task
    rarely pins the model's pose in world space (`printed_wall_hook` is
    explicitly modelled lying on its side for printing), so a hard-coded `"y"`
    would flag a perfectly good part. Set `"x"`/`"y"`/`"z"` only when the task
    text really does fix the orientation.
  - **`hole_axis_tol_deg`** (+ `min_hole_count`, `min_hole_dia_mm`,
    `max_hole_dia_mm`) — every hole whose diameter falls in the window must be
    within this many degrees of the mounting axis, i.e. **perpendicular to the
    mounting plane**, or the screws cannot go in (lesson L1). The diameter
    window selects the fasteners specifically: a bearing block's 22 mm bore is
    deliberately *not* perpendicular to its base, so cap it with
    `max_hole_dia_mm`. Bosses/pins and bores of unknown kind are not counted —
    only real holes. Axis direction is ignored (a bore has an orientation, not
    a direction).
  - **`protrusion_mm`** — how far the body must stand off the mounting surface.
    In `auto` mode this is the distance from the mounting face to the farthest
    point of the shape (reported by `feature_probe` as `standoff_mm`), **not** a
    bounding-box extent — a part lying diagonally has a large bbox in every
    axis while standing off nothing. This is the check that catches lesson L2:
    a silhouette built in the wrong projection plane stands off by only its own
    wall thickness.
  - **`min_slab_ratio`** — thinnest bounding-box dimension divided by the
    largest. A cheap, pose-independent detector for the same flat-plate
    failure; useful as a second opinion when a part has no clean mounting face.

  Note which failure each sub-check does and does not catch: on a flat plaque
  the screw holes genuinely *are* perpendicular to its largest face, so in
  `auto` mode `functional_hole_axes` passes and `functional_protrusion` /
  `functional_slab_ratio` are what fail. That is intended — they describe the
  same defect from different angles, and any one of them failing sinks the
  scorecard. `tests/test_functional_checks.py` pins this behaviour against the
  original broken-hook geometry.

## Adding a new golden task

1. Copy an existing file in `eval/tasks/` as a starting point.
2. Give it a unique `id` matching the filename (not enforced, but keeps
   things sane) and a `prompt` a user would plausibly type.
3. Pick a small set of `checks` that capture the task's *objective* success
   criteria — dimensions that must hold, and (if the model has a Parameters
   spreadsheet) one `parametric_probe` on the most important driving alias.
4. Don't worry about a model that doesn't yet exist failing every check —
   that's expected until you actually build/load it and grade again.
5. Validate the JSON parses: `python3 -c "import json; json.load(open('eval/tasks/<new>.json'))"`.

## Current golden tasks

- `m6_hex_nut.json` — M6 hex nut.
- `parametric_flange.json` — circular flange with a bolt-hole pattern.
- `pi5_enclosure_base.json` — Raspberry Pi 5 enclosure base tray.
- `simple_l_bracket.json` — simple L-shaped mounting bracket.
- `printed_wall_hook.json` — J-shaped FDM wall hook (printed on its side);
  exercises dimension + `print_readiness` checks together.
- `bearing_block_608.json` — 608 bearing pillow block with a press-fit bore
  and M5 mounting holes; exercises alias dimension checks plus
  `print_readiness`.
- `snap_fit_box.json` — two-part snap-fit enclosure (base box + cantilever-snap
  lid) for FDM. The **assembly** task: parts are two separate solids side by
  side on the plate, so it exercises the new `solid_count: {"expected": 2}`
  check, alias checks on `WallT`/`Clearance`, a `parametric_probe` on `WallT`,
  and `print_readiness`. **Note on `outer_length`:** because both parts sit side
  by side, the overall bounding box spans *both* of them, so no single overall
  bbox axis cleanly isolates one part's 60 mm length. The task pragmatically
  reads the overall bbox **X** extent and assumes both parts keep their 60 mm
  length along X and are laid out spread along Y/Z (the natural print layout);
  this is documented in the task's `comment` field and is intentionally not
  fully orientation-agnostic.

## Scorecard output

Markdown mode (default) prints one table per task: check name, ✓/✗/⚠,
expected vs. measured value, and a short note (e.g. the measured diff, or why
a check errored), followed by `Score: X/Y checks passed` (plus `(N skipped)`
if any check reported `skipped`, e.g. `print_readiness` against an older
bridge — skipped checks are excluded from both the numerator and denominator
of the score). `⚠` means the check itself could not run (a bridge command
errored) or was skipped as unsupported by the bridge — distinct from `✗`, a
check that ran and genuinely failed. `--json` emits the same information as a
JSON object (single task) or array (`--all`) for scripting/diffing between
harness versions.

## Results history (regression tracking)

Unless `--no-save` is passed, every graded task (single task or `--all`)
appends one JSON line to `eval/results/history.jsonl` (the directory is
created if missing):

```json
{"timestamp_iso": "2026-07-18T12:00:00+00:00", "task_id": "m6_hex_nut", "score": "6/6", "passed": 6, "failed": 0, "errors": 0, "skipped": 0, "checks": [{"name": "validate", "status": "pass", "expected": true, "actual": true}, ...]}
```

- `score` is `passed/graded` where `graded` excludes `skipped` checks.
- `checks` is a flat list across all check types for that task (same
  `check`/`status`/`expected`/`actual` fields as the scorecard, renamed to
  `name`/`status`/`expected`/`actual`).
- This file is machine-local and **not committed** —
  `eval/results/.gitignore` (`*`) excludes everything under `eval/results/`.
  The intended use is diffing `history.jsonl` between two harness-version
  runs (or grepping it over time) to see whether a harness change actually
  moved the needle, without polluting the repo with run artifacts.
- Pass `--no-save` to skip writing to history for a given invocation (e.g.
  a throwaway/manual check).

## Other flags

- `--discovery PATH` — override the bridge discovery file path (default
  `/tmp/freecad-agentsmith-bridge.json`). Mainly useful for testing run_eval.py
  itself against a nonexistent/fake path to exercise the "bridge
  unreachable" exit-2 path without touching the real bridge.

---

# End-to-end runner (`run_e2e.py`)

`run_e2e.py` is Layer 2 on top of `run_eval.py`. Where the grader only *scores*
an already-open model, the e2e runner drives a **full task**: for each golden
task it (1) creates and saves a fresh sandbox document, (2) builds a worker
prompt from the modeling harness + a compact bridge contract + the task prompt,
(3) launches a real backend CLI (`codex` / `claude`) as a subprocess to model
the task through the bridge, and (4) grades the resulting active document by
**reusing `run_eval.py`'s check logic** (`import run_eval; run_eval.grade_task(task)`).
Then it closes the sandbox document it created.

## ⚠️ Preconditions (this is NOT offline — it costs tokens)

Unlike `run_eval.py`, this runner launches a **real AI backend session** that
edits your live FreeCAD and consumes real API tokens. Before running it you must
arrange all of:

1. **A live FreeCAD with the AgentSmith bridge panel running**, in
   **Edit access + Python enabled** mode. `run_e2e.py` verifies this via `ping`
   (`access_level == "edit"` and `python_enabled == true`) and exits `2` with
   guidance otherwise.
2. **At least one document already open** in FreeCAD (File → New is enough). The
   bridge's `execute_python` is transactional and attaches to the *active*
   document, so one must exist for the runner to create its own sandbox document
   alongside it. Your document is never touched.
3. **The backend CLI installed and authenticated** on `PATH` (`codex` or
   `claude`), able to run non-interactively with the flags the panel uses
   (`codex exec … -s danger-full-access`, or
   `claude -p … --dangerously-skip-permissions`).
4. **Your consent to a real modeling session** — it drives an autonomous backend
   with full access for up to `--task-timeout` seconds per task, and bills tokens
   to your account. There is no dry-run of the backend.

**Safety rail:** the runner refuses to start (exit `2`) if any *non-sandbox*
document is open with unsaved changes, so an unexpected failure can never cost
you work. Pass `--force` to override. `run_e2e.py` only ever creates, edits and
closes documents it made in the sandbox dir — it never touches your other
documents.

## Sandbox-dir policy

All eval documents are created as `e2e_<task_id>.FCStd` in a dedicated **sandbox
directory** (default `eval/results/e2e-workdir/`, override with `--workdir`).
The runner never opens, saves-as, renames, moves or deletes anything outside
this dir, and closes only the exact document (matched by its FreeCAD internal
`Name`) it created for each task. Backend logs go to
`eval/results/e2e-logs/<task_id>.log` (combined stdout/stderr).

## What it deliberately drops vs. the production panel

`run_e2e.py` is a bare eval sandbox, not the supervised chat panel, so it
intentionally omits: the watchdog / SIGTERM budget enforcement (it uses a plain
subprocess timeout instead), checkpoints and undo-transaction bracketing,
reference images and "before" visual/screenshot context, persisted per-document
history, the reviewer/autofix second pass, and the protected-document guard
(unneeded — it only ever works on documents it created in the sandbox). It does
mirror the panel's harness assembly and the exact backend-CLI flag shapes from
`BridgeGui._backend_launch`.

## Running it

```bash
# Run every task with codex (default backend)
python3 eval/run_e2e.py --all --backend codex

# One task with claude + a specific model
python3 eval/run_e2e.py --task eval/tasks/snap_fit_box.json --backend claude --model opus

# Machine-readable output; custom sandbox dir and per-task timeout
python3 eval/run_e2e.py --all --json --workdir /tmp/e2e --task-timeout 600
```

Flags: `--all` / `--task PATH` (default `--all`), `--backend codex|claude`,
`--model <id>`, `--repeat N` (default 1), `--workdir DIR`,
`--task-timeout SECONDS` (default 900), `--discovery PATH`, `--force`,
`--json`, `--no-save`.

**`--repeat N` — use it for anything you intend to keep.** The backend is
stochastic: one run cannot distinguish a task that passes reliably from one that
passes a third of the time, so a single-run number is not a baseline, it is an
anecdote. Each repeat gets its own sandbox document (`e2e_<task>-run2.FCStd`),
its own log (`<task>-run2.log`), and its own record carrying `run_index`;
`compare.py` aggregates them into a pass rate.

## Comparing two snapshots (`compare.py`)

```bash
python3 eval/run_e2e.py --all --repeat 3 --json > eval/baselines/2026-07-24-codex.json
# ... change the harness ...
python3 eval/run_e2e.py --all --repeat 3 --json > /tmp/after.json
python3 eval/compare.py eval/baselines/2026-07-24-codex.json /tmp/after.json \
    --labels "baseline,new-harness"
```

Prints a per-task table of every check with its pass rate on both sides, marking
each as improvement / regression / new / removed, and exits `1` if anything
regressed — so it can gate a change in a script. `--json` emits the same
structure for further processing. It reads both runner shapes, so a `run_eval.py
--all --json` scorecard can be compared against an e2e snapshot.

Exit codes: `0` every task's every graded check passed; `1` at least one check
failed/errored (or a backend run failed); `2` preconditions not met (bridge
unreachable, wrong access level, no active document, or unsafe foreign documents
open) — nothing was run.

## Output

Default output is a Markdown scoreboard: one row per task (task, backend, model,
score, backend exit code, whether it timed out, wall time, log path), an overall
line, then the full per-task `run_eval` scorecard for each task. `--json` emits
the same per-task records as a JSON array. Unless `--no-save` is passed, one line
per task is appended to `eval/results/e2e-history.jsonl`:

```json
{"timestamp_iso": "2026-07-19T12:00:00+00:00", "task_id": "snap_fit_box", "backend": "codex", "model": "opus", "score": "8/8", "wall_time_sec": 412.7, "backend_exit_code": 0, "timed_out": false, "all_passed": true}
```

Like `history.jsonl`, this file lives under `eval/results/` and is git-ignored.
