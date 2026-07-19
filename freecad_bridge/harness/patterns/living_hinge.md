# Pattern: living hinge (thin flexure printed flat)

## When to use

A **one-piece flex hinge**: a thin web between two panels bends repeatedly instead of a
pinned joint. Use for box lids, clamshells, snap-closed cases, cable clips. It flexes
by bending a thin section, not by rotating a pin — for a rotating knuckle joint see
`hinge_clearance.md`; for a clip that latches see `snap_fit_cantilever.md`.

**Material matters more than geometry here:** use **PP, PETG, PA (nylon)**, or TPU.
**PLA cracks after a few cycles — do not use PLA for a living hinge.** PP is the classic
(near-infinite cycles); PETG/PA are the practical FDM choice. See `materials.md`.

## Key parametric relationships (aliases in `Parameters`)

- **Hinge thickness `HingeT` = 0.3–0.5 mm** (≈ **1–2 layers** at 0.2 mm). Thinner =
  lower bending strain per cycle = longer life, but too thin under-extrudes/tears. Aim
  for a clean 1–2 layer web.
- **Hinge length (flex zone) `HingeL` ≥ 5 × HingeT** so the bend strain spreads over a
  finite arc instead of a sharp crease. For a full 180° fold, longer is safer; a single
  ~0.4 mm web can crease-fail, so **use several parallel thin webs** (a "multi-web"
  hinge) or a broad blended fillet zone to distribute strain.
- **Bending strain** at the web ≈ `HingeT / (2·R_bend)` where `R_bend` is the local bend
  radius the fold forms. Keep it under the material's flex strain — PP/PA tolerate high
  strain, PETG moderate, **PLA almost none**. Longer flex zone → larger `R_bend` → lower
  strain → more cycles.
- **Blend fillets** from the full panel thickness down to `HingeT` on both sides (radius
  ≥ 1–2 mm) — a sharp step at the web root is the crack initiator.
- **Print orientation:** print **flat**, hinge in the XY plane, so the **flex bends
  along the layer lines, not across them** (bending across stacked layers delaminates
  immediately). The web should be continuous filament along the fold, not a stack of
  short layer segments.

## Cycle-life expectations (indicative, print-dependent)

- **PP:** effectively unlimited (10⁴–10⁶+ folds) — the reference living-hinge material.
- **PA (nylon):** thousands to tens of thousands of cycles.
- **PETG:** tens to low hundreds of gentle cycles — fine for an occasional lid, not a
  daily mechanism.
- **PLA:** ~single digits then cracks — avoid.

## FreeCAD build recipe

1. Aliases: `PanelT`, `HingeT` (=0.4), `HingeL` (=`5*HingeT` min), `BlendR` (=1.5),
   `WebCount` (1 or several).
2. Model the two panels at `PanelT`.
3. On the datum between them, sketch the thinned web: a pocket/loft that drops the
   section to `HingeT` over the `HingeL` flex zone.
4. **Fillet/blend** the transition from `PanelT` to `HingeT` with `BlendR` on both faces.
5. For a multi-web hinge, **linear-array** `WebCount` thin webs across the fold with full
   thickness between them.
6. Ensure the flat orientation is recorded so slicing keeps layers along the fold.

## Pitfalls

- **PLA (or any brittle filament)** → cracks in a handful of folds. Pick PP/PA/PETG.
- **Web printed so it bends across layers** → delaminates on the first fold. Print flat.
- **Sharp step at the web root** (no blend) → stress riser, tears there.
- **Web too thick** (> ~0.6 mm) → won't fold, or over-strains and whitens/cracks.
- **Web too thin / under one layer** → under-extrudes into a perforated, weak line.

## Verify

- `cross_section` across the hinge → confirm the web is `HingeT` (0.3–0.5 mm) and the
  blends are present; cite the measured thickness.
- `measure` flex-zone length ≥ 5 × HingeT and the blend radii.
- `check_solid` → the whole part is one watertight solid (the hinge must stay connected,
  not split into two shells).
- State the chosen material and that the part prints flat with the fold along layer lines.
