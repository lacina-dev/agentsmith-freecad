# Playbook: modeling a new shape from a verbal request

Goal: turn a natural-language description into native, parametric FreeCAD geometry.

## 1. Understand the target before touching geometry

- Restate the requested object in one sentence and list the *driving dimensions* it
  implies (overall size, wall thickness, hole diameters, counts, spacing, fillets…).
- If a dimension is required but not given and cannot be derived, pick a sensible,
  clearly-stated default rather than stalling — note the assumption in your report.
- Run `model_digest` to see what already exists so you extend the document instead of
  duplicating or fighting existing objects.

## 2. Establish the functional pose FIRST

Before any geometry, answer these four questions — a part whose dimensions are all
correct but whose functional axes disagree is scrap (a wall hook whose J-curl lies flat
against the wall holds nothing):

- **Mounting/contact surface:** which face touches the wall/table/mating part?
- **Fastener axes:** screws/pins must be **perpendicular to the mounting plane** and
  reachable by a tool when the part is in place.
- **Load vector:** which way does gravity/force act *when mounted*? The load path must
  run from the working feature into the mounting surface.
- **Working feature direction:** which way must the hook opening / slot / cavity /
  spout face *in use*? (e.g. a hook's opening faces up and away from the wall.)

State these in one short paragraph ("mounted on a vertical wall via 2 screws along −Y;
load −Z on the lip which projects +Y from the wall…"). Every later feature must be
consistent with this pose — check each planned feature against it. A profile extruded
in the wrong plane satisfies every dimension and still fails all four.

## 3. Write a feature plan + acceptance criteria

Before building any geometry, write down the plan — it is cheap insurance and becomes
the yardstick the `verify` playbook checks against.

- **Ordered feature plan:** list the native features in build order
  (sketch → pad → pocket → fillet → array …), and for each note **which Parameter
  drives it** (e.g. "Pad `Body` height ← `BodyHeight`"; "PolarArray 4× ← `HoleCount` on
  `BoltCircleDia`"). This forces a native, parametric tree instead of ad-hoc solids.
- **Measurable acceptance criteria:** state the checks that mean "done", each a number
  you can later confirm from geometry — e.g. "wall ≥ 2 mm", "4 holes on 58 mm pitch",
  "overall ≤ 80 × 60 × 30 mm", "single watertight solid". If references were used, add
  "axonometric render matches the reference target" (see the `reference` playbook).
- **Record this plan in your final report.** The `verify` playbook re-measures the
  finished model against these criteria before you may declare success.

## 4. Establish parameters first

- Create or reuse a `Spreadsheet::Sheet` (commonly `Parameters`) and add an aliased cell
  for every driving dimension. Geometry must reference these aliases via expressions, so
  the result stays editable.
- Group related parameters and give aliases descriptive names (e.g. `WallThickness`,
  `BoltCircleDia`), not `A1`-style opaqueness.

## 5. Build with native features

- Sketch → Pad/Pocket/Revolution → Fillet/Chamfer/Array, bound to the parameters above.
- Constrain sketches fully; verify with `sketch_info` (`fully_constrained: true`).
- Add features one at a time and `recompute`/`validate` between the risky ones so a
  failure points at the feature that caused it.

## 6. Verify against the request

- Pull the final numbers from `model_digest` and check each requested dimension.
- Change a driving parameter mentally (or actually, then revert) to confirm the model is
  genuinely parametric and not accidentally rigid.

Deliver a real mutation, validated and saved, per the core "Definition of done".
