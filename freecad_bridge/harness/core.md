# Core modeling harness

These rules apply to every live-editing task, regardless of task kind.

## Ground truth: how the model looks right now

- Before deciding anything, get the snapshot:
  `python3 freecad_bridge_client.py model_digest '{}'` — every object's bbox + size,
  volume, face/edge counts, placement, all Spreadsheet aliases and values, every Sketch's
  constraints and datums. Single source of dimensional truth; never rebuild those numbers
  with ad-hoc `execute_python` measuring loops.
- Rendered images are *shape and orientation* evidence — never read a dimension off one.
- For the **functional** facts a bounding box cannot express — where the holes point,
  which face the part sits on — use `python3 freecad_bridge_client.py feature_probe '{}'`:
  every bore/boss with axis direction, diameter, length and whether material lies outside
  it (= hole) or inside it (= boss), the planar faces with outward normals and areas, and
  the body's standoff from its largest flat face (`standoff_mm`). Use it instead of
  hand-rolled face loops — those numbers grade your work.

## Keep it native and parametric

- Preserve the FreeCAD feature tree. Prefer, in order: change a Spreadsheet parameter
  (`spreadsheet_set`) → change a sketch datum (`sketch_set_datum`) → change a property
  expression (`set_expression`) → change a plain property (`set_property`) → add a
  native feature (`create_object`) → `execute_python` only when nothing above fits. If a
  value drives geometry, that driver is what you change — never hard-code a one-off
  number that fits only today.
- Every mutation goes through the bridge so it lands in a FreeCAD Undo transaction.
- Never close/reopen the document, `saveAs`, rename/move/delete the FCStd, or launch a
  second FreeCAD process — the canonical file is under a live watchdog. **Never create
  a document either**: the task is bound to the one active at start (checkpoints,
  rollback and the file guard all point there); a new part gets a new Body, and if the
  name matters, set the document's `Label`.

## Engineering discipline (every model)

- **Units & precision:** millimetres, sensible precision (no 0.0001 mm float drift);
  relationships expressed symbolically in the Spreadsheet, not as pasted decimals.
- **Robust, editable tree:** anchor sketches to named datums/origin, never to
  auto-generated face/edge names (topological-naming breakage); sketches fully
  constrained; descriptive Labels; one Body = one watertight solid (`parametrics.md`).
- **Pick the manufacturing process early** (print / mould / sheet / machined): it
  dictates walls, draft, radii, tolerances and orientation — state it and design to it.
- **Functional datums first:** dimension mating/critical features from stable datums so
  tolerances don't stack; let non-critical dimensions float.
- **Mass / material budget:** know the material and check `mass_properties` — mass and
  centre of mass plausible for the use (stability, weight limits).

## Read the brief like a contract

Before any geometry, split the request into a numbered list R1…Rn — one line per literal
ask, compound sentences broken apart ("flat on its side, ≥ 3 perimeters, chamfered bottom
edges" = three requirements). Tag each **DIM** (a number to measure) · **PARAM** (an
alias that must drive geometry) · **BUILD** (process, orientation, slicer setting) ·
**SHAPE** (form, ergonomics, looks) · **REPORT** (a statement the user is owed: which
screws to buy, which variant you designed for). BUILD/SHAPE/REPORT are the rows that
vanish — no dimensional check catches them. Add what the request is silent about but the
object demands, marked `[implied]`.

- Modal words are literal: **"about N" = N ±5 %** and you state the achieved value;
  **"at least N"** is a floor — aim 5–10 % over, never under; **"max N"** is a ceiling;
  counts are exact. Vague adjectives (small, sturdy, ergonomic) become a number here,
  before the first sketch.
- **Nothing ever leaves the list.** What you cannot meet stays as **DEVIATION** with the
  reason and what you did instead; reinterpreting a requirement into an easier one is a
  failed task. Two asks in conflict ("fits a 280 mm roll" vs "≤ 200 mm tall"): name both,
  say which you subordinated — now, not in the post-mortem.
- Write it to `.agentsmith/requirements/<task_id>.md` as
  `R<n> | CLASS | text | status | evidence` and **end the final message with the same
  table** (REQUIREMENTS); the evidence each class needs is in `verify.md`. Every
  DIM/PARAM/BUILD row is also a feature-plan acceptance criterion with its R-number.
- **When the budget bites, the order is** mutation → DIM/PARAM evidence → BUILD gate
  (`print_readiness`; `slice_check` only if it fits) → pre-mortem → renders. Whatever you
  skip is named in REQUIREMENTS as a DEVIATION with the reason "not run" — never silently
  omitted.
- **Answer in the user's language.** Prose — the final message, deviation reasons, OPEN
  QUESTIONS — follows the language of the request (Czech prompt → Czech report, English →
  English, any other likewise). The fixed markers stay in English byte-for-byte: the
  `REQUIREMENTS` table with its `R<n> | CLASS | … | PASS/DEVIATION` columns, the `OPEN
  QUESTIONS` header, and the `ACTION-ONLY TASK COMPLETED: <summary>` line — they are
  parsed by machines, not read by people.

## Before any geometry: what does it hold, and what don't you know?

If the part holds, carries, mounts, covers or mates with **anything that exists in the
real world** — kitchen towel, phone, bearing, shelf, cable — write that object's
dimensions down BEFORE modelling, **even when you think you know them**: everyday objects
are where a confident guess goes wrong (lesson L9). Size for the **largest common variant
plus clearance** and say which variant you designed for — fitting only the smallest
version is broken for most users — and keep the numbers in `Parameters`, not in a sketch.

```
holds: kitchen towel roll — height 230–280 (CZ ~230, DE ~260, US 279) · Ø 105–150 mm
  → designed for 280 + 10 clearance = 290 mm inner   [TABLE reference-dimensions]
```

Then sweep six points for what the request does **not** tell you: **mating objects** ·
**load** (mass/force, direction, shock or static) · **mounting surface**
(plasterboard / brick / tile / wood / steel, thickness, fastener it takes) ·
**environment** (indoor/outdoor, UV, heat, water, food or skin contact — picks the
material) · **user and use** (mounting height, one-handed, wet or gloved hands) ·
**production constraints** (bed size, material on hand, part count). Sort every gap:

- **LOOKUP** — the fact exists somewhere. Get it NOW, before geometry, stopping at the
  first source that answers: `reference-dimensions` → reference images/URLs attached to
  this task → web search/fetch if this run has one → a datasheet or standard you can
  name. One honest attempt per gap; cap at ~3 lookups and ~10 % of the time budget.
- **DERIVABLE** — compute it and show the arithmetic.
- **ASSUME** — only the user can know (their preference, their wall). Never let a LOOKUP
  masquerade as an ASSUME; that is exactly how L9 happened.

Never stall for an answer — no human replies mid-run. Decide, state it, keep it cheap to
reverse: **every assumed number lives in one aliased `Parameters` cell named after the
unknown** (`RollHeight`, `MaxLoadKg`, `WallAnchorDia`), named in the report. An
assumption buried in a sketch is a defect.

## Every number carries its provenance

Tag every number in model and report: `GIVEN` · `MEASURED` (bridge geometry) ·
`TABLE <playbook>` (`reference-dimensions`, `print3d`, `materials`, `design`, …) ·
`FETCHED <url>` (a page you opened this run) · `STANDARD`/`DATASHEET <id>` · `RECALL`
(memory, unverified) · `ASSUMED`.

- Never print a URL you did not open, a standard you cannot number, or more precision
  than the source has — `RECALL` is honest, a fabricated citation is not. Keep ranges as
  ranges ("230–280, designed for 280"), never an invented single figure ("~247 mm").
- No web search/fetch tool this run? Say so once and tag those numbers `RECALL` — a
  cross-check that could not run is **unverified**, not "confirmed".

After REQUIREMENTS the final message carries **OPEN QUESTIONS** — one bullet per ASSUME:
the unknown, the value chosen, why, the `Parameters` alias carrying it, and what changes
if the user answers differently. Zero open questions on an underspecified request means
you invented certainty (2–5 is normal) — but do not pad it with settled numbers.

## Definition of done

1. The live document actually changed (a real mutation via the bridge).
2. `validate` passes.
3. Each body is a single watertight solid — confirm with `check_solid`.
4. Every requested dimension/tolerance is numerically confirmed from geometry
   (`model_digest` / `cross_section` / `measure`); assemblies with `interference_check`;
   physical sanity with `mass_properties` (`verify.md`).
5. The model meets the **feature-plan acceptance criteria** (`model.md` §3); with
   references, the **visual comparison** against them was done.
6. The **functional pose holds**: fastener axes ⊥ mounting plane, working feature faces
   the right way in use, load path reaches the mounting surface. A dimensional pass is
   not a functional pass — check it explicitly.
7. The result bodies are **Visible** (`set_visibility`), construction inputs and
   intermediate booleans hidden — a model the user cannot see reads as failure. Then
   `save`, `fit_view` and a final `screenshot`.
8. Every real-world object the part interacts with has **dimensions stated with a
   source** and the model checked against them — clearance and chosen variant included.
9. **Multi-part designs say how they stay together.** Per interface: what holds it
   (screw, insert, snap, press fit, threaded rod, glue), what stops it coming apart in
   use, what stops it rotating. "The parts touch here" is not a joint
   (`assembly.md` §3, `fasteners.md` §6).
10. Report the objects/parameters you changed, with before→after numbers.
11. The **design review** ran (`design.md` §7) — every line closed with a measured value,
    not "it looks fine".
12. The **`verify.md` close-out** ran: every R1…Rn PASS or DEVIATION with evidence, none
    dropped or reinterpreted; the failure pre-mortem per functional feature fixed or
    stated as a limit. The report ends with **REQUIREMENTS** and **OPEN QUESTIONS**,
    every number in it provenance-tagged.

## Match the part to its character and use

The whole library ships with every task and nobody pre-selects a mode — applying every
playbook that matches is **your** job: read the request for manufacturing process,
loading, fit and part type, and apply all that fit; several usually do. If the request is
silent but the object's nature implies a condition (a bracket carries load, a case is an
enclosure, a printed part must be support-minimal), apply it anyway and state the
assumption. `parametrics` and `verify` apply to **every** task.
