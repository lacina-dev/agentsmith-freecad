# Pattern: hinge clearance (print-in-place or pinned knuckle hinge)

## When to use

A knuckle (barrel) hinge that must rotate freely — either **print-in-place** (knuckles
printed already interleaved, no assembly) or **pinned** (separate pin/filament axle
through aligned knuckles). Use for lids, folding parts, doors. For a clip that flexes
instead of rotating, see `snap_fit_cantilever.md`.

## Key parametric relationships (aliases in `Parameters`)

- **Radial clearance** between pin and knuckle bore: **~0.3–0.5 mm** for FDM
  (`RadialClear = 0.4`). Below ~0.3 mm the parts fuse/bind on FDM; above ~0.5 mm the
  joint is sloppy. Process-dependent — **assume a 0.4 mm nozzle, 0.2 mm layers**;
  tighten for resin, loosen for coarse FDM.
- **Axial (gap) clearance** between adjacent knuckle faces: **~0.3–0.5 mm** so knuckles
  don't weld together in a print-in-place hinge.
- **Knuckle count:** odd total, alternating between the two leaves (e.g. 3 or 5); more
  knuckles = stronger, stiffer axis. Keep each knuckle length ≥ its OD so it doesn't
  cock on the pin.
- **Pin fit:**
  - Print-in-place captive pin: pin Ø = bore Ø − 2 × `RadialClear` (loose, free spin).
  - Separate axle (steel rod / filament): bore Ø = pin Ø + **0.1–0.2 mm** slip fit at
    the rotating leaf, and an interference/retained bore at the anchored leaf.
- **Knuckle OD ≈ 2–2.5 × pin Ø** for adequate wall around the bore.
- **Leaf-to-knuckle blend fillet** to avoid a stress riser at the root.

## FreeCAD build recipe

1. Aliases: `PinD`, `BoreD` (=`PinD + 2*RadialClear` for print-in-place, or
   `PinD + 0.15` slip), `KnuckleOD` (=`2.2*PinD`), `KnuckleLen`, `AxialClear`,
   `RadialClear`, `KnuckleCount`.
2. Sketch the knuckle circle Ø`KnuckleOD` on a datum on the hinge axis → **Pad** to
   `KnuckleLen`.
3. Sketch Ø`BoreD` concentric → **Pocket** through (the axle bore).
4. **Linear array** knuckles along the axis at pitch `KnuckleLen + AxialClear`,
   assigning alternate instances to each leaf.
5. Join each knuckle set to its leaf; **fillet** the leaf-knuckle root.
6. For print-in-place, model the captive pin at Ø`PinD` inside the bore with the gap.

## Pitfalls

- **Clearance too small** (< 0.3 mm FDM) → knuckles fuse, hinge won't move.
- Knuckles longer than the leaf can support → cock on the pin, bind.
- Even knuckle count / asymmetric split → uneven load, wobble.
- Bore referenced to a face, not the hinge-axis datum → array/edits break.
- Sharp leaf-knuckle junction → cracks in use.

## Verify

- `measure` bore Ø, pin Ø, radial gap and axial gap against the aliases.
- `interference_check` between the two leaves / pin: knuckles clear (no interference)
  yet the pin is captured — the gap is the intended `RadialClear`/`AxialClear`.
- `cross_section` across the knuckle stack: alternating knuckles, consistent gaps.
- `check_solid` on each leaf (and the pin) — each a single watertight solid.
