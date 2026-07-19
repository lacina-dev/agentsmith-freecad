# AgentSmith for FreeCAD

Local authenticated bridge and integrated AI chat panel for controlling the
document currently open in the FreeCAD GUI. It listens only on `127.0.0.1`
and creates a new random session token every time FreeCAD starts the bridge.

## Installation

Copy or symlink this directory as `AgentSmith` (or run `install.sh` from the repo root) inside the FreeCAD user `Mod`
directory, then restart FreeCAD. Select the **AgentSmith** workbench and press
**Start bridge** in its dock panel.

Discovery data is written to `/tmp/freecad-agentsmith-bridge.json` with permissions
`0600`. The bridge removes this file when stopped.

Python execution is disabled by default. Enabling it in the panel permits an
authenticated local client to execute FreeCAD Python API code, so leave it
enabled only during an active collaboration session.

The **Codex** tab can launch a separate local `codex exec` session and stream
its result inside FreeCAD. Its default workspace sandbox is safer but cannot
reach the live localhost bridge. Integrated Chat backends therefore always run
with full local access; the panel does not show a separate permission checkbox.
File checkpoints, validation, rollback, transaction and resource guards remain
active around every task.

Version 0.2 adds atomic batches with rollback, automatic checkpoints, undo and
redo, validation reports, event journaling, selection and visibility control,
view control, export, and structured object creation/removal.

Version 0.3 adds a supervisor around integrated Codex editing tasks. It refuses
live editing without explicit local-access permission, creates a mandatory
pre-task checkpoint, requires bridge-based editing, gives each mutation a
FreeCAD Undo transaction, compares document fingerprints, validates geometry,
rolls back failed changed tasks, and writes an audit trail to
`.codex-freecad/task-history.jsonl`.

Version 0.4 replaces the separate Bridge/Codex tabs with one Chat-first panel.
The compact bridge controls remain on the same page below the dominant chat
area. The Chat backend can be switched between locally installed Codex, Claude
Code, and GitHub Copilot CLI; all use the same checkpoint, transaction, Undo,
validation, fingerprint, rollback, screenshot, and audit supervisor.

Version 0.5 adds a backend-specific model picker. Codex models come from its
current account catalog/cache, Copilot models are parsed from the installed
CLI's current configuration help, and Claude uses its rolling model aliases.
FreeCAD preferences remember the selected backend and a separate last-used
model for each backend across panel reloads and application restarts.

Version 0.6 adds explicit task-state feedback: an indeterminate activity bar,
backend/model badge, elapsed timer, disabled selectors while a task runs, and
persistent finished states that distinguish supervisor-verified success,
failure/no verified model change, and user cancellation.

Version 0.7 protects the canonical `.FCStd` throughout an integrated task. It
keeps a verified in-memory snapshot, watches the live document and file path,
blocks close/open/save-as and file-moving Python calls through the bridge, and
atomically restores and reopens the snapshot if a backend closes the document,
changes its path, removes/corrupts the file, or produces an unverified result.
Every successful save is ZIP-integrity checked before a post-task checkpoint.

Version 0.8 adds a persistent timeline per `.FCStd` under `.freecad-history`.
Each task stores its request, backend response, backend/model, affected objects,
validation result, hashes, and before/after checkpoints. The effective timeline
is automatically supplied to every later backend task and shown again after a
FreeCAD restart. The Chat panel can restore the document to the checkpoint
before any recorded step; the restore itself becomes a branching history event.
Existing entries from `.codex-freecad/task-history.jsonl` are migrated on first
load of a document.

Version 0.9 makes an integrated edit one FreeCAD transaction instead of one
full Undo transaction per bridge request. It persists a pending history entry
before launching a backend, so an interrupted task remains attributable after
a process or machine crash. A safety supervisor stops and rolls back tasks that
run longer than eight minutes, grow FreeCAD memory by over 1 GiB, emit over
3000 mutations, or repeatedly mutate one object over 180 times.

Version 0.9.2 makes discovery an owned, self-healing lease. A running bridge
recreates missing or stale discovery metadata before launching a task and on
every supervisor tick, while an older/test server may only remove its own
token. Backend processes also start without AppImage `PYTHONHOME`, `PYTHONPATH`
and `PYTHONEXECUTABLE`, so the system Python bridge client remains runnable.

Version 0.10 adds backend-independent visual grounding. Before every task the
panel captures axonometric plus six orthographic views and writes a general
scene manifest containing object types, visibility, selection, shape types,
bounding boxes, solid counts and volumes. Codex receives the PNGs through its
native `--image` input and Copilot through `--attachment`; Claude is given the
same local paths and manifest in its prompt. A matching post-task view set is
captured for persistent before/after history. Prompts explicitly distinguish
visual evidence from dimensional proof and require task-specific numerical
verification through FreeCAD geometry.

Version 0.10.1 preserves and restores the exact original camera after context
capture, so creating views no longer leaves the user's model rotated or
reframed. All seven views remain in the manifest, while native backend image
attachments are capped at five. For Codex the positional prompt is placed before
the variadic `--image` options, preventing it from being parsed as another image
path. Raw merged backend output is persisted in task history as well, including
CLI/startup failures that occur before JSON events.

Version 0.11 adds an extensible modeling harness and a standardized way for a
task to learn how the model looks. A time-budget/delivery contract in every
prompt tells the backend to diagnose quickly and deliver a verified change
instead of investigating until the watchdog kills it. Task-kind playbooks live
in `harness/` (`registry.json` + Markdown files: `core`, `model`, `fix`,
`research`); the whole set is injected on every task (see Version 0.12 — the
selector was later removed), and new kinds are added by dropping a file and
registering it — no code change. A new
`model_digest` bridge command returns the canonical dimensional snapshot (per
object bounding box/size, volume, area, topology counts, placement; all
Spreadsheet aliases/values; all Sketch constraints/datums; overall bounding
box) and is embedded in every scene manifest. Reference snapshots are now
opt-in: checkboxes under the prompt select which views to capture (current
camera by default, plus optional top/bottom/front/rear/left/right), so a task
no longer always renders all seven. The user's camera is captured at task start
and restored when the task finishes, including after a rollback. The
`LIVE_EDIT_BUDGET_SECONDS` watchdog limit is a single named constant shared with
the prompt.

Version 0.11.1 adds a `print3d` harness playbook and a compact conditional block
in `core`: any task whose part is meant for FDM/FFF printing is steered toward
support-minimal geometry (45° overhang rule, ≤10 mm bridges, teardrop/chamfered
horizontal holes, flat-on-bed orientation and model splitting, and robust
minimum walls/detail/clearances), distilled from Prusa, Protolabs/Hubs, and
Markforged/Stratasys guidance.

Version 0.11.2 adds a `strength` harness playbook and a conditional block in
`core`: any load-bearing part is sized for strength — define the load case
(static/dynamic/fatigue/impact), keep `σ ≤ Re/FoS` with typical factors of
safety, use bending/section-modulus and generous fillets, and match the
manufacturing method (wrought vs cast/welded vs anisotropic FDM where load must
run in XY). It ships indicative property tables for common steels, aluminium,
plastics and FDM-printed plastics (with infill effect), distilled from standard
machine-design FoS references and Prusa/Markforged/MatterHackers data.

Version 0.12 turns the harness into an always-on engineering library and adds
measuring commands. The exclusive **Režim** selector is gone: every playbook is
now provided to the backend on every task, and the prompt instructs the agent to
apply each playbook whose trigger matches the part's character or intended use
(with `parametrics` and `verify` always applying). `registry.json` gains a
`trigger` per playbook, and `_harness_text` emits a generated library index plus
all playbook bodies. Eight new playbooks cover parametric robustness (avoiding
FreeCAD's topological-naming breakage, fully-constrained sketches, clean tree),
quality-assurance verification, tolerances/fits (ISO 286/2768, print-realistic
clearances), assemblies/interfaces, fasteners/threads/snap-fits (metric tap,
clearance, self-tap boss, heat-set insert tables), enclosures/housings,
injection-moulding DfM (draft, uniform walls, rib/boss ratios), and sheet metal
(R≥T bends, K-factor, relief, hole-to-bend). `core` gains an engineering-
discipline section (units/precision, robust tree, process choice, functional
datums, mass budget), a "match the part to its character/use" section, and a
verify-before-done contract. Four new read-only bridge commands back the QA
checklist: `mass_properties` (volume/CoM/mass), `measure` (min distance between
two shapes), `check_solid` (single watertight solid), and `interference_check`
(pairwise overlap volume between solids). Adding a new playbook is still just a
Markdown file plus a registry entry — no code change.

## Reference inputs, task budget, and cross-sections (0.13.0)

The chat panel gained a **Reference** field: paste local image paths and/or
`http(s)` URLs (one per line). Local images are copied into
`.agentsmith/reference/<task_id>/`, recorded in a `reference-manifest.json`,
attached to the backend as extra vision input alongside the auto-captured
before-screenshots, and described in the prompt as a *visual target only* (never
dimensional truth). URLs are listed for the agent to fetch. A bad reference is
logged and skipped — it never aborts a task.

A **Rozpočet** (budget) selector replaces the fixed 8-minute wall-clock limit
with three presets — *Rychlá úprava* (8 min / 480 s), *Nový díl* (15 min /
900 s), *Sestava* (25 min / 1500 s). The choice is persisted and drives both the
prompt's delivery contract and the watchdog in `_check_task_guard`.
`LIVE_EDIT_BUDGET_SECONDS` (480) remains the default fallback.

The new read-only `cross_section` command returns numeric internal-geometry
evidence without mutating the document (safe in read-only mode). Args: optional
`object`/`objects` (default: all visible shapes), `axis` (`x`|`y`|`z`, default
`z`), and `position` (default: the target's bounding-box midpoint along that
axis). Per object it returns the closed-wire count, each wire's length and
bounding box, the section bounding box, and a best-effort filled `section_area`;
nested wire bounding boxes let you infer e.g. wall thickness.

## Print readiness and Claude vision (0.14.0)

The new read-only `print_readiness` command returns numeric FDM-printability
evidence without mutating the document (safe in read-only mode). Args: optional
`object`/`objects` (default: all visible shapes), `up_axis` in
`{x,y,z,-x,-y,-z}` (default `z`, the **build direction**), and `overhang_deg`
(default `45.0`, the maximum self-supporting angle measured **from vertical**).

Angle convention (also embedded in the response's `convention` field): for each
face the outward normal `n` is taken at the face parameter-range midpoint and
normalized (a `Reversed` face flips `n`). A face is down-facing when
`dot(n, up) < 0`, and its overhang angle is
`theta = degrees(acos(-dot(n_hat, up_hat)))` where `theta == 0` is a
straight-down horizontal underside (worst) and `theta == 90` is a vertical wall.
A down-facing face **needs support** when `theta < 90 - overhang_deg`. A flat
(`theta ~= 0`) face sitting at the shape's minimum up-coordinate is bed contact,
counted as `bottom_area` rather than an overhang.

Per object it returns `total_face_area`, `bottom_area`, `overhang_faces`,
`overhang_area`, `overhang_area_pct`, `min_bbox_dim`, the shape `bbox`, and up
to 20 `worst_overhangs` (`{area, angle_deg, center}`, most horizontal first).

Example call: `{"command": "print_readiness", "args": {"up_axis": "z",
"overhang_deg": 45}}`.

The `export` command writes via `Part.export`, whose format is chosen from the
target path extension — it already writes **STL** (as well as STEP/IGES/BREP/OBJ
and other Part-supported formats), so slicer integration can reuse it as-is.

The Claude backend now receives attached visuals too: since the `claude` CLI has
no `--image` flag, the launcher prepends an `ATTACHED IMAGES` header listing each
image path to the prompt, instructing Claude Code to open them with its
image-capable Read tool before modeling (feature flag `claude_vision_via_read`).

## Protocol

Each connection sends one newline-terminated JSON request and receives one
newline-terminated JSON response. Requests contain `token`, `command`, and an
optional `args` object.

Use the `capabilities` command for the current command list. Major additions
include `batch`, `checkpoint`, `list_checkpoints`, `undo`, `redo`, `history`,
`validate`, `events`, `selection_set`, `set_visibility`, `create_object`,
`remove_object`, `set_view`, `export`, `model_digest`, `mass_properties`,
`measure`, `check_solid`, `interference_check`, `cross_section`, `view_state`,
and `view_restore`.
