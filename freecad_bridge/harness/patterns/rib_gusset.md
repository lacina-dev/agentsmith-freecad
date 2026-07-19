# Pattern: stiffening rib / gusset

## When to use

A wall, boss, bracket or overhanging feature needs to be **stiffer or stronger without
thickening the nominal wall** (thick walls cause sink marks when molded and warp/long
prints when 3D-printed). Ribs add bending stiffness; gussets brace a boss or a
right-angle joint.

## Key parametric relationships (aliases in `Parameters`)

Let `Wall` = nominal wall thickness.

- **Rib thickness ≤ 0.6 × Wall** at its base (`RibT = 0.5 * Wall` is the safe default).
  This is the **sink-mark rule**: a rib thicker than ~60% of the wall pulls a visible
  sink on the opposite (show) face when molded. Printed parts are more forgiving but
  keep ~0.5× for consistency and to avoid over-thick cores.
- **Rib height ≤ 3 × Wall** per rib. Deeper stiffening → use **several shorter ribs**,
  not one tall one (tall thin ribs buckle and are hard to fill/print).
- **Draft ~0.5–1° per side** on molded ribs (mandatory for ejection); the tip ends up
  thinner than the base — keep the *base* within the 0.6× rule.
- **Spacing ≥ 2 × Wall** between ribs so the region between them fills/cools; a grid or
  cross pattern resists bending in two directions.
- **Root fillet ≈ 0.25–0.5 × RibT** where the rib meets the wall — reduces the stress
  riser and eases filling; don't over-fillet or you recreate a thick section.
- **Gusset** (triangular brace under a boss/wall): same thickness rule (`≤ 0.6 × Wall`);
  size the legs to ~0.5–1× the boss height it braces.

## FreeCAD build recipe

1. Aliases: `Wall`, `RibT` (=`0.5*Wall`), `RibH`, `RibDraft`, `RibFillet`,
   `RibSpacing` (=`2*Wall` min).
2. Sketch the rib cross-section (or use a single line + the **Rib** tool) on a datum
   plane through the wall → **Pad** to `RibH`, thickness `RibT`, symmetric.
3. Apply **draft** `RibDraft` to the rib side faces if molded.
4. **Fillet** the rib root with `RibFillet`.
5. **Linear/polar array** ribs at `RibSpacing`, or mirror a cross pattern; for a boss
   gusset, place the brace between boss OD and wall.

## Pitfalls

- **Rib as thick as the wall** → sink mark / heavy core. Enforce the 0.6× rule.
- One **tall thin rib** instead of several short ones → buckles, hard to fill.
- No draft on a molded rib → ejection drag / scuffing.
- Over-large root fillet → recreates the thick junction you were avoiding.

## Verify

- `measure` rib base thickness and confirm `RibT / Wall ≤ 0.6`.
- `cross_section` across the ribbed region: rib base within the rule, spacing sound, no
  merged thick section.
- `check_solid`: ribs merge into the parent solid, still one watertight body.
- For load cases, sanity-check stiffness improved (see `strength`).
