# Playbook: fasteners, threads and snap-fits

Goal: correct hole/boss/thread sizing so screws, inserts and snaps actually work.
Prefer parametric holes driven from the fastener size in the `Parameters` Spreadsheet.

## 1. Threads: model vs cosmetic

- For most functional parts, **do not model full helical threads** — they bloat the
  model and rarely matter for print/mould. Model the correct **tap/clearance hole** and
  note the thread (e.g. "M4 tapped") in the Label/spreadsheet.
- Model real threads only when they must mate a printed/moulded thread or are the point
  of the part; then use the Thread feature / helix sweep.

## 2. Metric hole sizes (mm)

| Screw | Tap (cut/formed thread) | Clearance (close–normal) |
|-------|-------------------------|--------------------------|
| M3    | 2.5                     | 3.2 – 3.4                |
| M4    | 3.3                     | 4.3 – 4.5                |
| M5    | 4.2                     | 5.3 – 5.5                |
| M6    | 5.0                     | 6.4 – 6.6                |

Add a counterbore/countersink for socket/flat-head screws as needed.

## 3. Self-tapping / thread-forming into plastic bosses

- **Boss outer diameter ≈ 2.0–2.4 × screw diameter.**
- **Pilot hole** slightly smaller than the screw major diameter:
  M3 ≈ 2.4–2.6, M4 ≈ 3.1–3.5, M5 ≈ 3.8–4.2 mm.
- Blend the boss to the wall with a fillet (~25% of wall) and add a small lead-in
  chamfer at the hole mouth.

## 4. Heat-set / press inserts (recommended holes, verify datasheet)

| Insert | Hole diameter (mm) |
|--------|--------------------|
| M3     | 4.2 – 4.4          |
| M4     | 5.8 – 6.0          |
| M5     | 7.0 – 7.3          |
| M6     | 8.3 – 8.8          |

Boss OD ~2× insert hole; leave a small lead-in chamfer and enough boss depth.

## 5. Snap-fits (cantilever)

- Keep peak **strain within the material limit**: ~1–2% for common rigid plastics
  (tough grades allow more — check the datasheet). Strain ε ≈ 1.5·δ·t / L² (δ = deflect,
  t = beam thickness, L = beam length).
- Longer/thinner beams flex more safely for a given deflection; add a generous **root
  fillet** to avoid a stress riser; add a lead-in ramp and a defined engagement face.
- For living hinges (PP/PE), keep the hinge thin (~0.3–0.5 mm) and blend it in.

## 6. Reach for hardware before inventing geometry

The default for anything structural is a **standard fastener**, not a printed
feature. Screws, nuts, threaded rod, heat-set inserts and dowel pins are cheap,
stronger than anything FDM produces, and let a part be taken apart again. Printed
threads and clips are for light, non-structural, occasionally-opened things.

The **Fasteners and Gears workbenches are installed** and drivable headlessly —
see the "Real fasteners & gears (workbenches)" playbook for verified calls. In a
complex assembly the real screws belong in the model: they prove the fastener fits,
the head clears and the thread reaches, and they make the design legible. Model the
**holes, counterbores and nut pockets natively** — those are the printed part — and
let fastener objects represent the bought hardware.

## 7. Verify & deliver

- Confirm hole/boss sizes with `measure`; confirm clearance holes don't interfere with
  the fastener path. Report the fastener spec and the modelled hole/boss sizes.
