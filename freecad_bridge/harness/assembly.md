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

## 3. What actually holds it together

Write this down for every interface before you model it — a table of three columns:

| interface | what holds it | what stops it coming loose / rotating |
|---|---|---|
| lid ↔ body | 4× M3 screw into heat-set insert | screw preload; 2 dowel pins stop rotation |
| arm ↔ base | M5 threaded rod through both, nut each end | nut + printed shoulder stops axial slide |
| bracket ↔ wall | 2× countersunk screw | second screw stops it pivoting |

Two failure modes this catches, and both are common in printed parts:

- **"They touch, therefore they hold."** A face resting on a face is not a joint.
  If nothing resists the load, the part opens up in use.
- **A single fastener is a hinge.** One screw holds a part on and lets it swing.
  Anything that must keep its orientation needs a second screw, a pin, a
  non-round spigot or a flat.

Prefer real hardware over printed features where the load matters. Printed threads
strip, printed clips fatigue, printed pins shear — a **screw into a heat-set insert**,
a **nut in a captive pocket**, or a **threaded rod running the length of the part** all
survive far more than the geometry they replace.

## 3b. Threaded rod as reinforcement

Printed plastic is weak between layers, and that is exactly where a long part
breaks. A steel rod through it turns a layer-adhesion problem into a steel problem:

- Run **M4/M5 threaded rod** down the axis that sees bending or tension, through a
  hole of nominal + 0.3–0.5 mm, with a nut and washer at each end pulling the part
  into compression.
- Put the rod where the tensile stress is — the outside of the bend, not the centre —
  and keep at least 3 mm of wall around it.
- Design the nut pockets so the nut cannot rotate, or the user cannot tighten it.
- Say in your report which fastener the user must buy: length, thread, count.

## 4. Joints and motion

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
