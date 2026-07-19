# Playbook: designing for FDM 3D printing (support-minimal and strong)

Apply whenever the part will be printed on a filament (FDM/FFF) printer. Goal: a part
that prints cleanly with minimal support **and** is strong in the direction it is
loaded. Values are typical for a **0.4 mm nozzle, ~0.2 mm layers**; treat as defaults
and scale with the real nozzle/line width and material. Pick the **material first**
(`materials.md`) — it sets overhang behaviour, warp risk and minimum walls.

## 1. Orientation strategy — decide this FIRST

Orientation is the single biggest lever on strength, surface, supports and time. Choose
it before detailing geometry, and **state the chosen build orientation in the report.**

- **Strength before looks:** put the **strongest direction (in-plane / XY) along the
  principal stress**. Layer lines must **not run across bending tension** — a printed
  beam loaded to bend must have its layers running along its length, not stacked across
  the tension face (that face peels layers apart; see the anisotropy knockdown in
  `materials.md` and load path in `strength.md`).
- **Flat, stable base:** largest flat face on the plate for adhesion and to remove the
  base overhang; a stable footprint won't tip mid-print.
- **Minimal supports:** pick the orientation that minimizes overhang area and support
  contact on cosmetic/critical faces (quantify with `print_readiness`, §7).
- These three often conflict — **strength usually wins** for a load-bearing part; note
  the trade-off you made. If no single orientation works, **split the part** along a
  hidden seam into support-free sub-parts and rejoin with dowels/pegs (~0.2–0.3 mm
  clearance on mating pins).

## 2. Strength: walls and infill (perimeters do the work)

- **Perimeters/walls carry most of the load, not infill.** Use **3–4+ perimeters** for
  structural parts (≈ 1.2–1.6 mm of solid shell at 0.4 mm line width). Adding perimeters
  beats raising infill for stiffness and strength per gram.
- **Infill 15–40 % typical**; ~20 % is a fine default. **100 % infill is rarely
  justified** — it costs time/warp for little gain once walls are adequate; reserve it
  for small, highly-loaded solids.
- **Solid top/bottom layers count** as structure — 4–6 solid layers close the shell and
  add bending stiffness to flat parts.
- A strong printed part = **thick shell (perimeters + solid faces) around modest infill**,
  oriented so the shell takes the load in-plane.

## 3. Overhangs, bridges, holes

- **Self-supporting ≤ ~45–55° from vertical** — steeper (more horizontal) down-faces sag
  and need support. Replace 90° ledges with a **45° chamfer** or a fillet that keeps the
  local slope ≤ 45°. Turn "T" overhangs into "Y" slopes (YHT rule).
- **Bottom edges: chamfer, not fillet.** A fillet on a down-facing edge becomes a
  shallow overhang that droops; a 45° chamfer stays self-supporting.
- **Horizontal holes** (axis parallel to bed) sag at the top: best is to orient the hole
  **vertical**; else make the top a **teardrop / diamond** (~45° peak) or add a 45°
  chamfer at the top of the bore. Small horizontal holes (Ø ≲ 6–8 mm) print round enough
  as-is.
- **Bridges** (flat spans across a gap) print reliably to **~5–10 mm** (up to 15–25 mm
  with strong cooling at the cost of surface). Keep unsupported spans **≤ ~10 mm**, below
  5 mm safest; else arch the underside ≤ 45°, split, or accept support.

## 4. Dimensional compensation (design around FDM's biases)

- **Vertical holes print undersize** (stair-stepping + flow): oversize the modelled Ø by
  **+0.2–0.4 mm**, or design for **drilling/reaming** critical bores to size.
- **Elephant's foot** (squished first layers bulge out): add a **0.3–0.5 mm × 45°
  chamfer on bottom edges** so the base still fits its mating dimension.
- **Clearances between parts:** ~**0.2–0.3 mm** for a horizontal press/snug fit,
  **0.4–0.5 mm** for a free sliding/moving fit. Holes and slots need more than
  bosses/pins because both surfaces shift inward. Link `tolerances.md` for fit classes.

## 5. Minimum robust features (0.4 mm nozzle)

- **Wall ≥ 2 perimeters (~0.9–1.2 mm)**; structural walls ≥ 3–4 perimeters. Make walls a
  multiple of line width so perimeters pack cleanly with no thin gap-fill.
- **Pins/posts ≥ 3 mm** diameter — thinner ones are fragile and wobble while printing.
- **Embossed/engraved detail:** raised text ≥ **2 mm tall** and features ≥ 0.6 mm wide;
  **engraved ≥ 0.6 mm deep**. Engrave rather than emboss very fine detail.

## 6. First layer, adhesion, warping, seams

- **Broad flat base** beats tiny contact points (sharp points on the bed lift).
- **Warp-prone materials** (ABS/ASA, big flat parts) pull corners up: **round or chamfer
  corners**, add a **brim/mouse-ears**, keep an enclosure, avoid large unbroken flat
  bottoms. PLA/PETG warp little.
- **Seam placement:** the perimeter start-point seam is a cosmetic and slight weak line —
  put it on a hidden/inside edge; keep it off sealing surfaces and away from the
  highest-tension fibre where possible.

## 7. Verification tie-in (numeric, not eyeballed)

- **`cross_section`** through walls/pockets → proves true wall thickness and that
  cavities exist (section area, closed-wire count, section bbox). Cite the numbers, not
  an outside screenshot.
- **`print_readiness`** → returns the **overhang faces exceeding a ~45° threshold and
  their areas, per up-axis**. Run it for candidate orientations to pick the one with the
  least support-needy area, and to prove numerically that no unsupported face is too
  steep. (State the up-axis you chose.)
- **`export` to STL** exists for handing the solid to a slicer for future
  print-time/support preview.
- Keep every rule above **driven by `Parameters` aliases** (overhang angle, wall,
  clearances, chamfers) so print-readiness stays editable.

## Slice check (real slicer feedback)

`print_readiness` and `cross_section` are geometric estimates; the **slicer is
ground truth**. Once the model passes the geometry checks above, run the real
slicer and read what it actually produces. From the project directory:

```
python3 freecad_bridge/slice_check.py --from-bridge       # slice the live model
python3 freecad_bridge/slice_check.py part.stl --json     # or an exported file
```

It exports the current model (via the bridge), slices it headless for the
configured printer (Prusa MK3.x / PETG, see `slicer-config.json`) and reports
**estimated print time, filament used (g / mm / cm3), layer count, whether
supports were generated, and any slicer warnings** (e.g. object off bed, empty
layers). Exit 0 = sliced; 1 = the slicer rejected the geometry (read the log
tail — often a non-watertight mesh, fix per `fix.md`); 2 = setup problem.

- **Supports generated on a part meant to print support-free is a defect, not a
  statistic.** The slicer adds threshold-based support only where a down-face is
  too steep — so `supports_generated: YES` (or non-zero overhang/bridge regions)
  means the geometry still has an overhang the design was supposed to remove.
  Go back to **§1 orientation** and **§3 overhangs/bridges/holes** (re-orient,
  chamfer the ledge, teardrop the hole, split the part) and re-slice until it is
  support-free — or consciously accept the support and say so.
- **Investigate every warning** before reporting success; a warning is the
  slicer telling you the print will misbehave.
- **Put the numbers in the final report:** print time, filament grams, layer
  count, supports yes/no. They are the strongest printability evidence you have
  because they come from the real slicer, not a geometric guess.

## 8. Printability checklist (the `verify` playbook can reference this)

1. **Orientation stated**, principal load in-plane, layers not across bending tension.
2. Largest flat face on the plate; stable footprint.
3. No unsupported down-face steeper than ~45° (or chamfered/teardropped) —
   confirm with `print_readiness`.
4. No unsupported bridge > ~10 mm.
5. Bottom edges chamfered (elephant's foot + no down-facing fillet).
6. Walls ≥ 2 perimeters (structural ≥ 3–4); pins ≥ 3 mm; text ≥ 2 mm tall / 0.6 mm.
7. Vertical holes oversized +0.2–0.4 mm or flagged for reaming; fits use printed
   clearances (~0.2 snug / 0.4–0.5 free).
8. Warp control on large flat ABS/ASA (rounded corners / brim).
9. `cross_section` confirms real wall thickness; `check_solid` is one watertight solid.

## Sources (corroborated across multiple references)

- Prusa Knowledge Base — designing for FDM, overhangs/bridges/supports, orientation,
  teardrop holes, elephant's foot.
- Protolabs & Hubs — FDM design guidelines (min wall, min hole/pin, clearances, holes
  print undersize, text height).
- Markforged / Stratasys — orientation and the YHT rule; perimeters-vs-infill and
  anisotropy from Prusa/MatterHackers mechanical testing.
