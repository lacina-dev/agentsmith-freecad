# Pattern: threaded boss (screw into a molded/printed boss)

## When to use

A screw drives into a raised cylindrical boss — either **self-tapping / thread-forming**
into the plastic, or a **machine screw** into a tapped/insert hole. Common for lids,
PCB standoffs, and joining half-shells. (For brass inserts see `heat_set_insert.md`;
for the through-part clearance side see `screw_counterbore.md`.)

## Key parametric relationships (aliases in `Parameters`)

Let `ScrewD` = screw nominal (major) diameter.

- **Boss OD ≈ 2.0–2.5 × ScrewD** (`BossOD = 2.2 * ScrewD` is a safe default). Below 2×
  the wall is too thin and splits on thread engagement; above 2.5× wastes material and
  risks sink marks if molded.
- **Pilot hole** depends on how the screw engages:
  - Self-tapping/thread-forming: pilot ≈ **0.75–0.85 × ScrewD** (e.g. M3 ≈ 2.4–2.6,
    M4 ≈ 3.1–3.5 mm). Too large strips; too small cracks the boss.
  - Machine screw, cut thread: use the tap-drill size (M3→2.5, M4→3.3, M5→4.2 — see
    `fasteners`).
- **Engagement depth ≥ 2 × ScrewD** of threaded length for full pull-out strength.
- **Wall-to-boss blend:** fillet root ≈ **0.5 × wall thickness**; if the boss is tall
  or side-loaded, add a **rib/gusset** to the wall (see `rib_gusset.md`) rather than
  fattening the boss (avoids sink marks).
- **Boss base larger than boss OD** is unnecessary; a fillet at the base handles the
  stress transition.

## FreeCAD build recipe

1. Aliases: `ScrewD`, `BossOD` (=`2.2*ScrewD`), `PilotD` (per screw type),
   `BossHeight`, `BossFillet` (=`0.5*WallThickness`).
2. Sketch a circle Ø`BossOD` on the mounting face/datum, concentric with the screw
   axis → **Pad** to `BossHeight` (or up to the mating face).
3. Sketch a concentric circle Ø`PilotD` → **Pocket** to the engagement depth (add a
   short lead-in chamfer at the mouth so the screw starts straight).
4. **Fillet** the boss-to-wall junction with `BossFillet`.
5. Optionally add a rib per `rib_gusset.md`; array bosses to the bolt pattern.

## Pitfalls

- **Solid thick boss** → sink marks (molding) / long print + warp. Keep the boss a wall
  wrapped around the hole, not a solid cylinder, when it is large.
- Pilot **bottomed out** — leave clearance below the screw tip so it doesn't jack the
  boss apart.
- Pilot referenced to a face name instead of the screw-axis datum → breaks on edits.
- Boss not tied to the mating face by expression → gap when the shell height changes.

## Verify

- `measure` boss OD, pilot Ø and engagement depth against the aliases.
- `check_solid`: boss merges into one watertight solid (no stray shell).
- `cross_section` through the boss axis: one closed wire ring, wall (BossOD−PilotD)/2
  is sound, no accidental full-solid.
- If it joins two parts, `interference_check` that pilot/clearance holes align.
