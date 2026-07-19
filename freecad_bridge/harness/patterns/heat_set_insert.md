# Pattern: brass heat-set insert boss

## When to use

A **brass threaded insert** is melted (soldering iron / press) into a plastic boss to
give durable, reusable machine-screw threads — the standard for FDM enclosures that
open repeatedly. Distinct from `threaded_boss.md` (screw straight into plastic): here
the hole receives the **insert**, and the screw then threads into the brass.

## Key parametric relationships (aliases in `Parameters`)

Sizing is per the insert datasheet; these are conservative defaults — **verify against
the specific insert** (e.g. McMaster / Ruthex / CNC Kitchen) and record the part in the
report.

Let `ScrewD` = screw size the insert accepts (M3, M4…).

- **Boss hole Ø = slightly UNDERSIZE vs the insert OD** so molten plastic reflows and
  grips the knurl. Typical starting holes:
  - M3 insert → **Ø 4.0–4.2 mm**
  - M4 insert → **Ø 5.6–5.8 mm**
  - M5 insert → **Ø 6.4–6.7 mm**
  (These are the *melt* holes; they are a touch smaller than the press-fit numbers in
  the `fasteners` table — heat-set relies on reflow, not interference.)
- **Boss OD ≈ 2 × hole Ø** (min wall ≈ 0.5 × hole Ø around the insert) so the boss
  doesn't split as brass displaces plastic.
- **Boss/hole depth ≥ insert length + 0.5 mm** so the insert seats fully and the screw
  tip has clearance below it.
- **Lead-in chamfer** at the mouth **~0.5–1 mm × 45°** (or a short counterbore to the
  insert OD) to locate the insert square before it melts in.

## FreeCAD build recipe

1. Aliases: `ScrewD`, `InsertHoleD` (per table), `InsertLen`, `BossOD` (=`2*InsertHoleD`),
   `BossDepth` (=`InsertLen + 0.5`), `LeadIn` (=0.75).
2. Sketch Ø`BossOD` on the mounting face/datum, concentric with the screw axis →
   **Pad** to the boss height.
3. Sketch Ø`InsertHoleD` concentric → **Pocket** to `BossDepth`.
4. **Chamfer** the hole mouth `LeadIn` × 45° (or counterbore to insert OD for a
   self-locating start).
5. Fillet the boss-to-wall root (~0.5 × wall) / add a rib if tall (`rib_gusset.md`).

## Pitfalls

- Hole sized to the **screw** instead of the **insert** → insert won't seat / spins.
- Boss wall too thin → splits as brass displaces plastic (keep ≥ 0.5 × hole Ø).
- **No lead-in** → insert goes in crooked, thread ends up off-axis.
- Blind hole with no tip clearance → insert bottoms out proud of the face.
- Hole referenced to a face name, not the screw-axis datum → breaks on edits.

## Verify

- `measure` hole Ø, boss OD and depth against the aliases and the insert datasheet.
- `cross_section` through the boss axis: hole Ø correct, wall ≥ 0.5 × hole Ø, depth
  clears the insert length.
- `check_solid`: single watertight boss.
- Confirm the mating screw's clearance hole in the other part aligns
  (`interference_check`) and lands on the insert axis.
