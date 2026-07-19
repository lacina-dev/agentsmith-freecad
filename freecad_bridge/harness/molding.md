# Playbook: design for injection moulding (DfM)

Goal: parts that can actually be moulded — the dominant constraints are draft, uniform
walls and avoiding thick sections. Values are typical starting points.

## 1. Draft (mandatory)

- Add **draft to all faces parallel to the pull direction: 1–2° per side** minimum;
  use **3–5°** for textured surfaces and deep ribs. No vertical walls in the pull
  direction.
- Decide the **pull direction / parting line** early; orient features so they draw.

## 2. Uniform wall thickness (most important)

- Keep walls **uniform, typically 1.0–3.0 mm**. Non-uniform walls cause **sink marks,
  warping and voids**.
- Where thickness must change, **transition gradually** (taper), never a step.
- **Core out** thick sections instead of leaving solid mass.

## 3. Ribs and bosses

- **Rib thickness = 50–60% of the adjoining wall** (thicker ribs sink); rib height
  ≤ ~3× wall; add draft and a small root fillet.
- **Boss wall ≤ 60% of the nominal wall**; blend to the wall with a **fillet ~25% of
  wall**; support tall bosses with gussets/ribs rather than thickening.

## 4. Corners and details

- **Fillet internal and external corners** (internal radius ≥ ~0.5× wall) to even out
  flow and reduce stress — no sharp corners.
- Avoid undercuts where possible (they need side-actions/lifters); if unavoidable,
  flag them.

## 5. Verify & deliver

- Check walls are uniform (`measure` a few sections), all draw faces have draft, thick
  blobs are cored.
- Put wall thickness, draft angle and rib/boss ratios in the `Parameters` spreadsheet.
- Report draft angle, wall thickness, parting direction and any undercuts.
