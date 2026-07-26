# Core modeling harness

These rules apply to every live-editing task, regardless of task kind.

## Ground truth: how the model looks right now

- Before deciding anything, get the standardized model snapshot:
  `python3 freecad_bridge_client.py model_digest '{}'`
  It returns every object with its bounding box + size, volume, face/edge counts and
  placement, all Spreadsheet aliases and values, and every Sketch's constraints and
  datums. Treat this as the single source of dimensional truth — do NOT reconstruct
  the same numbers with ad-hoc `execute_python` measuring loops.
- The rendered images you were given are visual evidence of *shape and orientation
  only*. Never read dimensions off an image; always confirm numbers from `model_digest`,
  `object_info`, `sketch_info`, or `spreadsheet_info`.
- For the **functional** facts a bounding box cannot express — where the holes point and
  which face the part sits on — use:
  `python3 freecad_bridge_client.py feature_probe '{}'`
  It returns every bore/boss as one entry (axis direction, diameter, length, and whether
  material lies outside it = hole or inside it = boss), plus the planar faces with their
  outward normals and areas, and how far the body stands off its largest flat face
  (`standoff_mm`). Use it instead of hand-rolled `execute_python` face loops: the same
  numbers grade your work, so measuring them the same way removes a whole class of
  disagreement between you and the reviewer.
- If a value drives geometry (a Spreadsheet cell, a sketch datum, an expression), that
  driver is what you change — never hard-code a one-off number that happens to fit the
  current model.

## Keep it native and parametric

- Preserve the FreeCAD feature tree. Prefer, in order: change a Spreadsheet parameter
  (`spreadsheet_set`) → change a sketch datum (`sketch_set_datum`) → change a property
  expression (`set_expression`) → change a plain property (`set_property`) → add a
  native feature (`create_object`) → `execute_python` only when nothing above fits.
- Every mutation must go through the bridge so it lands in a FreeCAD Undo transaction.
- Never close/reopen the document, `saveAs`, rename/move/delete the FCStd, or launch a
  second FreeCAD process. The canonical file is protected by a live watchdog.

## Engineering discipline (every model)

- **Units & precision:** work in millimetres; keep dimensions to sensible precision
  (avoid spurious 0.0001 mm drift from floats). Express relationships symbolically in
  the Spreadsheet rather than pasting rounded decimals.
- **Robust, editable tree:** anchor sketches to named datums/origin, not to
  auto-generated face/edge names (avoids FreeCAD's topological-naming breakage); keep
  sketches fully constrained; give bodies/features descriptive Labels; one Body = one
  watertight solid. (See the "Parametrická robustnost" playbook.)
- **Pick the manufacturing process early** (print / mould / sheet / machined). It
  dictates walls, draft, radii, tolerances and orientation — state it and design to it.
- **Functional datums first:** dimension mating/critical features from stable datums so
  tolerances don't stack up; let non-critical dimensions float.
- **Mass / material budget:** know the material and check `mass_properties` — confirm
  mass and centre of mass are plausible for the part's use (stability, weight limits).

## Camera etiquette

- The panel captures and restores the user's camera around the whole task, and each
  `screenshot`/`set_view` you request is transient. Still, leave the view tidy: finish
  with `fit_view` so the user returns to a framed model.

## Before any geometry: what does it hold?

If the part holds, carries, mounts, covers or mates with **anything that exists in
the real world** — a roll of kitchen towel, a phone, a bearing, a shelf, a cable —
write down that object's dimensions BEFORE modelling:

```
holds: kitchen towel roll
  height  230–280 mm (CZ ~230, DE ~260, US 11" ~279)  [reference-dimensions playbook]
  Ø       105–150 mm
  designed for: 280 mm + 10 mm clearance = 290 mm inner height
```

Three rules, and the third is the one that gets skipped:

- **Look it up even when you think you know it.** Everyday objects are exactly where
  a confident guess goes wrong, because nothing prompts you to check. A holder built
  for a 250 mm roll fits neither a German roll nor an American one.
- **Size for the largest common variant, then add clearance** — and say which variant
  you designed for. A part that fits only the smallest version is broken for most users.
- **Put those numbers in the `Parameters` spreadsheet**, not inline in a sketch, so
  the assumption is visible and adjustable instead of buried in geometry.

Sources: your own knowledge is a starting point, not an answer — state the number,
then confirm it with a web lookup if you have the tools, or with a second independent
recollection if you do not. Record where each number came from in your report.

## Definition of done

1. The live document actually changed (a real mutation via the bridge).
2. `validate` passes.
3. Each body is a single watertight solid — confirm with `check_solid`.
4. Every requested dimension/tolerance is numerically confirmed from geometry
   (`model_digest` / `measure`); assemblies checked with `interference_check`;
   physical sanity checked with `mass_properties`. (See the "Kontrola kvality" playbook.)
5. The model meets the **feature-plan acceptance criteria** (see the "Modelování"
   playbook); if references were used, the **visual comparison** against them was done.
6. The **functional pose holds**: fastener axes ⊥ mounting plane, working feature
   faces the right way in use, load path reaches the mounting surface. Dimensional
   pass does not imply functional pass — check it explicitly.
7. The result bodies are **Visible** (`set_visibility`) — hide construction inputs and
   intermediate booleans, but a finished model the user cannot see reads as a failure.
   Then `save`, `fit_view`, and a final `screenshot`.
8. Every real-world object the part interacts with has its **dimensions stated with a
   source**, and the model is checked against them — including the clearance and which
   size variant it was built for.
9. **Multi-part designs say how they stay together.** For each interface: what holds
   it (screw, heat-set insert, snap, press fit, threaded rod, glue), what stops it
   coming apart in use, and what stops it rotating. "The parts touch here" is not a
   joint. (See the "Sestavy" and "Spojovací materiál" playbooks.)
10. Report the exact objects/parameters you changed, with before→after numbers.

## Match the part to its character and use

The full playbook library is provided with this task (see the "Harness library" index).
It is **your responsibility** to recognise the part's character and intended use and to
apply every relevant playbook — nobody pre-selects a mode for you. In particular:

- Read the request for cues about **manufacturing process** (3D print, injection
  moulding, sheet metal, machined), **loading** (does it carry static/dynamic load?),
  **fit** (does it mate with other parts, screws, inserts?) and **type** (enclosure,
  bracket, mechanism…). Apply the matching playbook(s); several usually apply at once.
- If the request is silent but the object's nature strongly implies a condition (e.g. a
  bracket is load-bearing, a case is an enclosure, a printed part needs support-minimal
  geometry), apply that knowledge anyway and state the assumption in your report.
- `parametrics` and `verify` apply to **every** task; the rest are conditional.
