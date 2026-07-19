# Playbook: assemblies and interfaces of multiple parts

Goal: parts that fit together, move as intended, and don't collide.

## 1. Define interfaces first

- List the **mating interfaces** (which face/hole meets which) and the intended
  relationship: fixed, sliding, rotating, snap, fastened.
- Model each part in its **own Body/document** as a single solid, positioned in a
  shared coordinate system (common origin / datums) so relative position is meaningful.
- Drive shared dimensions (bolt pattern, mating bore, pin diameter) from **one source**
  (a shared Spreadsheet or master skeleton sketch) so both sides stay consistent.

## 2. Clearances (starting points, tune to process)

- **Snug / located fit:** ~0.2 mm gap (printed) — parts fit with light effort.
- **Free / moving fit:** ~0.4–0.5 mm gap (printed) — free movement, tolerance for print
  variation.
- **Press / interference:** negative clearance, sized per material (see tolerances).
- Add clearance on **both** sides of a moving interface, not just one.

## 3. Joints and motion

- For pins/shafts use a clearance fit; for hinges leave axial and radial play.
- Constrain motion with real geometry (shoulders, stops) rather than relying on the
  user to position parts.
- If using the Assembly workbench, add joints that reflect the real DoF; otherwise
  place parts by shared datums.

## 4. Verify

- Run `interference_check` between mating parts: moving/clearance interfaces must show
  **no interference**; press-fits show the intended overlap.
- `measure` the critical gaps to confirm the designed clearance is present.
- Recompute after a parameter change and re-check that nothing starts to collide.

## 5. Deliver

- Report each interface, its fit type and measured clearance, and confirm no
  unintended collisions.
