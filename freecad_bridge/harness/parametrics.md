# Playbook: robust, editable parametric FreeCAD models

Goal: build models that stay valid and predictable when parameters change — the
difference between a "quality functional model" and one that explodes on the next
edit. This is mostly about avoiding FreeCAD's topological naming problem.

## 1. Avoid the topological naming problem (most important)

- **Anchor sketches and features to stable references**, not to auto-generated
  geometry names. Sketch on **named datum planes/axes/points** or the origin planes,
  not on a face/edge that a later feature can renumber.
- When you must reference an edge/face (fillet, chamfer, attachment), prefer a
  **datum** or a clearly stable reference, and keep such features **late** in the tree.
- Use a **master sketch / skeleton** (a driving sketch or Spreadsheet) that other
  features reference, so intent lives in one stable place.
- One PartDesign **Body = one contiguous solid**; don't fight the tree with stray
  booleans when a native feature exists.

## 2. Parameters and expressions

- Put every driving dimension in a `Parameters` Spreadsheet with **descriptive
  aliases**; reference them via expressions. No magic numbers buried in sketches.
- Derive dependent values by expression (e.g. `hole_r = bolt_d/2 + clearance`) so one
  edit propagates correctly.
- Keep expressions unit-consistent; avoid float drift by expressing relationships
  symbolically rather than pasting rounded decimals.

## 3. Fully constrained sketches

- Every sketch must be **fully constrained** (`sketch_info` → `fully_constrained:
  true`); no floating DoF, no accidental over-constraint.
- Constrain to the origin/datums for a stable, symmetric base; use symmetry
  constraints so the model scales cleanly.

## 4. Clean, legible feature tree

- Give features and bodies **descriptive Labels** (not `Pad001`), in a logical order.
- Group related objects; keep construction geometry marked as construction.
- After each risky feature `recompute` and check there are **no errors/warnings**
  (`validate`), so a failure points at the feature that caused it.

## 5. End state

- The result recomputes clean and is a **single watertight solid** per body.
- Change a key parameter, recompute, confirm the model updates sanely, then revert —
  proof the model is genuinely parametric, not accidentally rigid.
