# Baselines — committed eval snapshots

Unlike `eval/results/` (machine-local, git-ignored run artifacts), everything in
this directory is **committed**. A baseline is the reference point every later
harness change is measured against; it is worthless if it only exists on one
machine.

## What belongs here

One JSON file per full `run_e2e.py` sweep that is worth keeping:

```
<date>-<backend>-<model>.json      e.g. 2026-07-24-codex-gpt-5.json
```

Each file holds the per-task records `run_e2e.py --json` emits (task id, score,
per-check status, pass-rate across repeats, backend, model, wall time). Add a
baseline when:

- the harness changes in a way that should move quality (a new playbook, a
  rewritten rule, a new check), or
- a backend/model default changes underneath us.

## How to use one

```bash
# capture (costs real tokens — see eval/README.md preconditions)
python3 eval/run_e2e.py --all --repeat 3 --backend codex --json > eval/baselines/<date>-codex-<model>.json

# compare two snapshots
python3 eval/compare.py eval/baselines/<older>.json eval/baselines/<newer>.json
```

A single run per task is noise: the backend is stochastic, so a task that passes
once and fails twice is a different fact from one that passes three times. Use
`--repeat 3` for anything you intend to commit.

## What is here now

`2026-07-24-*-wall_hook.json` — a **model A/B spot check**, not a baseline: one run
of `printed_wall_hook` per model, taken right after the functional checks landed.

| model | score | wall time |
|---|---|---|
| `codex` / `gpt-5.6-sol` | 10/10 | 128 s |
| `claude` / `claude-opus-5` | 10/10 * | 965 s |

\* The stored Opus record shows `functional_hole_axes` failing. That is the grader,
not the model: its run was graded with `min_hole_angle_deg = 300`, which rejected the
self-supporting **teardrop** bores the print3d playbook asks for — measured at exactly
270°. The threshold now defaults to 240 and re-grading the same live model gave
2 holes, 0 misaligned. The record is kept unedited so the correction stays visible.

Both models produced a functionally correct hook: mounting plate against the wall,
bores perpendicular into it, arm standing 38–44 mm off the wall. The interesting
difference is cost, not quality — 7.5x wall time for the same score on this task.

**This is one run per model, so it cannot separate skill from luck.** Replace it with
a real baseline (`--repeat 3` over all seven tasks) before drawing conclusions.

## First full sweep — 2026-07-25

`2026-07-25-codex-gpt-5.6-sol-sweep1.json` — all seven golden tasks, one run each,
`gpt-5.6-sol`. **56/59 checks, 5/7 tasks clean, 14 minutes**, no timeouts, every
backend exit 0.

Its job was to smoke-test the task specs before spending anything on repeats, and
it earned its keep: **all three failures were in the eval, not in the models.**

| failure | verdict |
|---|---|
| `bearing_block_608` / `bore_diameter` | Grader: `abs(22.0 - 21.9)` is `0.10000000000000142`, so a value exactly at `tol: 0.1` failed. Fixed with a 1e-9 epsilon — and the check was rewritten as `mode: max`, because "sized undersize for a press fit" is a ceiling, not a window. |
| `bearing_block_608` / `seat_width` | Spec: the task says "a seat **at least** 8 mm wide" but the check was a ±3 window around 10, so a perfectly good 30 mm block failed. Now `mode: min`. |
| `snap_fit_box` / `outer_length` | Spec: with both parts side by side the overall bbox spans both, so the X extent is a layout choice (measured 130 for a 60 mm box). The task's own comment already admitted this. Now `mode: min`. |

`...-sweep1b-fixed-specs.json` re-runs those two tasks against the corrected specs.
Both spec fixes hold, and what remains are **real model findings**, stated precisely:

- `bore_diameter`: the bore came out at nominal 22.0 mm twice in a row — a slip fit,
  not the press fit the task asks for. Repeatable, so it is a harness gap worth a
  `lessons.md` entry, not noise.
- `solid_count`: the snap-fit run left two intermediate construction bodies visible
  alongside the two finished parts — a direct violation of the harness's own
  definition of done ("hide construction inputs and intermediate booleans").

Note the two `snap_fit_box` runs failed on *different* checks. That is the argument
for `--repeat` in one line: this task is the least stable in the set, and a single
run cannot tell you which failure is characteristic.
