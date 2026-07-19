# Pattern: dovetail slide / joint

## When to use

Two parts join or slide along a **dovetail**: a trapezoidal (flared) male rail captured
in a matching female groove, so they lock **against pull-apart** perpendicular to the
slide while sliding freely along it. Use for tool holders, modular rails, drawer
runners, removable mounts, wall-plate keys. For a clip that latches see
`snap_fit_cantilever.md`; for a rotating joint see `hinge_clearance.md`.

## Key parametric relationships (aliases in `Parameters`)

- **Flank angle `FlankAngle` = 45–60°** from the base (measured off the slide plane).
  Shallower than ~40° starts to pull out; steeper than ~60° loses the mechanical
  interlock and behaves like a plain tongue. **45–50° is the sweet spot** and prints
  well (the undercut flanks stay near self-supporting when oriented right).
- **Clearance `Clear` per flank/side:**
  - **Sliding fit: 0.2–0.3 mm per side** (free slide, e.g. a repositionable holder).
  - **Press/assembly fit: 0.05–0.1 mm per side** (snaps on once, stays put).
  Apply the clearance to the **female groove** (grow it) or split it between both;
  remember FDM prints holes/slots undersize, so a groove modelled to nominal ends up
  tight — bias the clearance a touch larger on FDM. Link `tolerances.md`.
- **Male width & depth:** rail base width `DoveW`, depth (height) `DoveH` ~0.5–1×
  `DoveW`; deeper = stronger interlock but more flank area to slide/print.
- **Stop feature (required):** a positive end stop — a back wall, a pin, or a bump —
  so the slide can't run off the end. Without it the parts separate at the open end.
- **Lead-in chamfer** at the entry mouth of the groove (~1 mm × 45°) to start the rail
  square. Optional small **detent/bump** for a click-in retained position.

## Print orientation

- **Orient so the slide direction lies in-plane (XY)** and the dovetail's long axis runs
  along the bed — the sliding surfaces are then smooth (layer lines run along the slide,
  not stacked as ridges across it) and the flanks avoid steep unsupported overhangs.
- Print the male rail with its wide base down where possible; the ≤ ~45° flanks are
  self-supporting. A groove printed mouth-up avoids bridging the slot roof.

## FreeCAD build recipe

1. Aliases: `DoveW`, `DoveH`, `FlankAngle` (=50), `Clear` (=0.25 slide / 0.075 press),
   `SlideLen`, `LeadIn` (=1.0).
2. Sketch the **male** trapezoid cross-section (base `DoveW`, flanks at `FlankAngle`,
   height `DoveH`) on the end datum → **Pad** along the slide to `SlideLen`.
3. Sketch the **female** trapezoid = male profile **offset outward by `Clear` on each
   flank and the top** → **Pocket** into the mating part along `SlideLen`.
4. Add the **stop** (back wall pad or pin) at the closed end, and the `LeadIn` chamfer at
   the open end.
5. Drive both profiles from the **same `DoveW`/`FlankAngle`** so the pair stays matched;
   only `Clear` differs between them.

## Pitfalls

- **Same nominal profile for both parts** (no clearance) → won't assemble or seizes.
- **Flank angle too shallow** (< ~40°) → pulls out under load; **too steep** (> ~60°) →
  no interlock.
- **No stop** → the slide runs off its open end.
- **Wrong orientation** → sliding faces are cross-layer ridges (rough, high-friction) or
  the flanks need supports that ruin the sliding surface.
- Groove modelled to nominal on FDM → ends up too tight; add clearance for print undersize.

## Verify

- `measure` male base width, female opening, and confirm the per-side gap = `Clear`
  (0.2–0.3 slide / 0.05–0.1 press); check `FlankAngle` on both parts.
- `interference_check` male in female → engages and slides with the intended clearance,
  locks against pull-out, and the **stop** limits travel.
- `cross_section` across the joint → matched trapezoids, uniform flank clearance, no
  over-thin groove wall.
- `check_solid` on each part.
- State the chosen orientation (slide direction in-plane) in the report.
