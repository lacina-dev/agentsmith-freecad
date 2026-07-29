# Playbook: designing for FDM 3D printing (support-minimal and strong)

Apply whenever the part will be printed on filament (FDM/FFF). Defaults assume a
**0.4 mm nozzle, 0.45 mm line, 0.2 mm layers**. Pick the **material first**
(`materials.md`) — it sets overhangs, warp and walls.

## 1. Orientation first — chosen before the first sketch

Biggest lever on strength, surface, supports and time; a **design input, not a slicer
setting**. State it in the feature plan (`model.md` §3) before any geometry, as
`build axis | bed face | layer direction | show face`.

- **Model in the print pose:** document **+Z is the build direction** — `print_readiness`
  and the graders default to `up_axis: z`. Pick sketch planes so the part is *born* in that
  pose; rotating a Placement afterwards drags every datum with it. If the functional pose
  differs, keep the print pose, state both, and verify the functional one by axis per
  `verify.md` (L1/L2 are about axes, not about which way up you print).
- **On an extruded-profile part printability is a property of the sketch:** every
  down-facing segment within 45° of vertical, every open span inside the §2d budget.

### 1b. Score the six poses numerically, on the blocked-out solid

Before chamfers and cosmetics. `up_axis` is a *view* on the same geometry — no
rotation, no rebuild, six read calls:
`print_readiness '{"up_axis": "z"}'`, then `-z`, `x`, `-x`, `y`, `-y`.

Tabulate `overhang_area_pct`, `bottom_area`, `worst_overhangs[0].angle_deg` per axis; the
table goes in the report as evidence the pose was chosen, not inherited from whichever
plane you sketched on. Then rank:

1. **Reject `bottom_area == 0`** (rests on an edge or point), `bottom_area` < 400 mm²
   above 40 mm tall, or height : smallest footprint dimension worse than **4 : 1** —
   unless you add a printed foot or state a brim.
2. **Reject** any pose putting principal tensile/bending stress across the layers (Z/XY
   knockdown 0.3–0.9, `materials.md`). Strength beats support count on a loaded part.
3. Among survivors: **lowest `overhang_area_pct`**.
4. Tie-break on cosmetics: visible, sealing and mating faces off the bed and above all
   **off support** — support scars a face and destroys its tolerance, so bearing seats
   and sliding/sealing faces stay self-supporting even if the rest is not.

**§1 vs §1b:** plan a pose in the feature plan, block out the solid **in it**, then sweep.
If another axis wins, **re-sketch so the winner becomes +Z** before detailing — never
rotate a Placement, and never deliver a model whose build axis is not `z`.

## 2. Overhangs, bridges, holes

### 2a. 45° is the default, 60° is earned

The limit is how much of each layer lands on the one below:
**θ_max ≈ atan(0.5 · line_width / layer_height)**, θ from **vertical** — 0.45/0.20 →
**48°**, 0.45/0.15 → **56°**, 0.45/0.10 → **66°**.

| material / cooling | usable θ from vertical |
|---|---|
| PLA, full part cooling | 55–60° |
| PETG | 45–50° |
| ABS/ASA/PC (enclosed, low fan) | 40–45° |
| TPU or any damp filament | ≤ 40°, short runs |

- **Design to 45°** unless all three hold: the material row allows more, layer
  ≤ 0.15 mm, face neither cosmetic nor loaded.
- **Cut self-supporting slopes at 40–42°, not a hard 45°** — tessellation and slicer
  rounding push an exact 45° face over the line.
- Raising `overhang_deg` above 45 **loosens** the check (flagged when
  θ < 90 − `overhang_deg`): pay for a claim past 45° with `slice_check`
  `supports_generated: false`, never by relaxing the threshold.

### 2b. Every down-facing feature gets a 45° roof

| down-facing feature | support-free form |
|---|---|
| ledge, shelf, step | 45° chamfer underneath, full overhang depth |
| boss/lug/pin off a wall | 45° gusset (`patterns/rib_gusset.md`) |
| bottom edge on the bed | 0.4–0.6 mm × 45° chamfer (§5) |
| down-facing **fillet** | chamfer of the same offset — a fillet turns horizontal at its lowest tangent |
| horizontal bore Ø > 8 mm | teardrop or diamond top (§2c) |
| counterbore / nut-trap ceiling | bridge inside the §2d budget, else a 45° cone |
| roof of an internal void | pitched roof (two 45° slopes to a ridge) or a cone |

Build **overhang-removing** geometry (ledge roofs, gussets, bore crowns) into the sketch or
as a Pad taper, **not a `Chamfer` on face/edge names** — one a rebuild drops takes
printability with it. **Exception: the bed-edge chamfer** — those edges are the extruded
sketch outline, unreachable from the sketch, and a taper would draft the whole wall, so a
`Chamfer` feature is correct there; drive it from `ChamferBottom` and re-apply it if a
rebuild drops it.

### 2c. Horizontal bores: find them with `feature_probe`

Every bore with **|dot(axis, up)| ≤ 0.35** (within ~20° of horizontal) must roof itself:

- **Ø ≤ 6 mm** round as-is; **Ø 6–10 mm** 45° chamfer at the bore top or an oval crown.
- **Ø > 10 mm or must stay round** — **teardrop** (two 45° tangents to an apex **1.41 · R
  above centre**) for a rod/screw through it; **diamond** (top arc → two 45° flats) where
  something flat sits above it.
- **Bearing seat, sliding fit, sealed bore** — neither is acceptable: rotate the bore
  vertical, or **split on its centre plane** into two half-bores facing up (§2e).

### 2d. Bridge span budget

A bridge is a flat span **anchored at both ends at the same height**; one starting on a
slope or on another bridge droops.

| material / cooling | max span | design to |
|---|---|---|
| PLA, full cooling | 25 mm | ≤ 15 mm |
| PETG | 15 mm | ≤ 10 mm |
| ABS/ASA/PC, enclosed | 10 mm | ≤ 6 mm |
| TPU, damp filament | 5 mm | avoid |

Sag grows with span² and **a bridge underside is never dimensionally accurate** — no fit,
seal or datum there. Over budget: arch the underside ≤ 45°, add a mid-span rib or pillar,
use a **pointed arch** (two 45° slopes to a ridge, no bridging), or split.

**Captive voids** (nut trap, magnet pocket) are roofed by a bridge: model a
**sacrificial ceiling 1–2 layers thick (0.2–0.4 mm)** across the void's **smaller**
dimension, then the geometry above it. A hex trap bridged corner-to-corner instead of
across flats collapses.

### 2e. When no pose works: split — the trigger is numeric

Split when, **after** the chamfer pass, the best pose still gives `overhang_area_pct`
> 15 %, `slice_check` puts support on a cosmetic/functional face, or the part exceeds the
bed.

- Each half needs a **flat bed face and no down-face steeper than 45°** — split through
  the worst overhang, a horizontal bore's centre plane, or a shadow line where the seam
  reads as a design line, and keep the joint **out of peak tension**. Re-run the §1b sweep
  **per half**: a good split makes both support-free; if not, the plane is wrong.
- Say what joins the halves — "they touch" is not a joint (L10) and one pin is a hinge:
  **≥ 2 alignment pins** (Ø4–6 mm, depth ≥ 1.5 × Ø, **0.15–0.25 mm** clearance, all of it
  in the hole) plus a glue face ≥ 200 mm² with a 0.3 × 1 mm squeeze-out groove; or screws
  into inserts; or a rod through both (`assembly.md` §3). Key it so it assembles one way
  only; report what the user must buy.

## 3. Dimensional compensation (FDM's biases)

- **Vertical holes print undersize:** oversize Ø by **+0.2–0.4 mm**, or design for
  drilling/reaming critical bores to size.
- **Clearances:** the fit table lives in `tolerances.md` §4 — use it, do not invent
  numbers; holes/slots need more than bosses/pins (both surfaces shift inward).

## 4. Minimum robust features (0.4 mm nozzle)

**Nothing thinner than one extrusion survives, and the slicer drops it silently.** Walls
are whole multiples of line width — **0.9 / 1.35 / 1.8 mm**; 1.0 mm prints as 0.9 plus a
gap-fill scar. **Perimeters carry the load, not infill:** 3–4 perimeters on a structural
part, infill 15–40 % (20 % default), 4–6 solid top/bottom layers.

| feature | minimum | structural / safe |
|---|---|---|
| wall | 0.9 mm (2 lines) | 1.6–1.8 mm (4 lines) |
| free-standing pin/post | Ø3 mm, **height ≤ 8 × Ø** | Ø4–5 mm |
| printable hole | Ø2 mm (below that it closes) | Ø3 mm+ |
| slot / gap | 0.8 mm | 1.2 mm |
| embossed text (never on a down-face) | cap 2 mm, **stroke ≥ 0.8 mm**, relief 0.4–0.6 mm | 3 mm / 1.2 mm |
| engraved text | depth ≥ 0.4 mm (2 layers) | 0.6 mm |

`print_readiness` returns **`min_bbox_dim`** per object: under 3 mm the *whole part* has a
fragile dimension, not just a detail — reconsider the geometry.

## 5. First layer, adhesion, warping, seams

- **All bed-touching faces coplanar**, from one alias — feet at different heights rock
  and fail the first layer.
- **0.4–0.6 mm × 45° chamfer on every bottom edge**: the elephant's-foot bulge is
  0.1–0.3 mm, so anything smaller disappears into it.
- **Bed contact ≥ 400 mm²** (`bottom_area`), height : smallest footprint dim **≤ 4 : 1**;
  past that add a printed foot, widen the base or state a brim.
- **ABS/ASA/PC:** bottom corners **R ≥ 3 mm** (warp lifts start at sharp corners), no
  unbroken flat bottom wider than ~100 mm, enclosure stated.
- **Surface quality follows orientation.** Best → worst: **vertical wall ≈ bed face** >
  flat top > up-slope ≥ 30° > up-slope < 20° (stair-steps) > down-slope > **any face
  support touched** (scarred, tolerance gone); keep what the user sees and touches in the
  top half. A fillet on a **vertical** edge is smooth, on a **horizontal** one it
  stair-steps: fillet vertical edges for looks, chamfer horizontal ones to print.
- **Seam:** the perimeter start leaves a line up the part — hide it in a sharp concave
  corner, a 0.5 mm cosmetic groove or the back face, off sealing/sliding faces and the
  highest-tension fibre, and say where you put it.

## 6. Acceptance gate: `print_readiness` + slice check

Geometry is an estimate, the **slicer is ground truth**: pass all four, numbers in the
report.

1. **`print_readiness`** at the real build axis, `overhang_deg` = the angle you claim
   (45 default): `overhang_area_pct` **≤ 10 %** (the eval budgets 15–25 %, so this leaves
   margin); `bottom_area` **> 0**; **no `worst_overhangs` entry with `area` ≥ 25 mm²** at
   the `overhang_deg` you claim — the angle bar is already encoded in `overhang_deg`, and
   entries under 5 mm² are tessellation noise. Read it right:
   **`angle_deg` is from straight-down — 0° = flat underside (worst), 90° = vertical wall
   (fine)**; the list is worst-first, so fix `worst_overhangs[0]` and re-run.
2. **Slice check** — the real slicer, run from the project directory, next to
   `freecad_bridge_client.py` (L5): `python3 slice_check.py --from-bridge --json`.
   It slices the live model for the configured printer (`slicer-config.json`) and reports
   print time, filament (g / mm / cm³), layer count, `supports_generated`, `support_lines`,
   `overhang_wall_regions`, `bridge_regions`, warnings. Exit 1 = geometry rejected
   (usually a non-watertight mesh → `fix.md`); 2 = setup.
   **`supports_generated: false` is the pass/fail**; the region counts locate residual
   risk (bridges over hole roofs are normal, a rising overhang-wall count is not).
3. Surviving support is an **argued decision with a number**: how many `support_lines`,
   which face they land on, why re-orienting, chamfering and splitting were worse. Support
   scars the face it touches 0.1–0.3 mm proud — **never on a mating, sealing, sliding or
   show face**; reorient or split rather than sand it afterwards.
4. **Slicer warnings: zero, or each one explained.**

**Fix in this order:** re-orient (§1b, free) → chamfer/teardrop the offender (§2b, §2c) →
arch, rib or sacrificially bridge the span (§2d) → split (§2e) → accept support with an
argument. Re-run `print_readiness` after every geometry change, `slice_check` **once at
the end** (it costs minutes). Clean at 45° but still sliced with support → suspect **mesh
tessellation** (§2a) before re-modelling. Keep every value above on a `Parameters` alias
and prove wall thickness with `cross_section`.

## 7. Final sweep (`verify` can reference this)

Orientation table (§1b) → down-faces roofed (§2b) → bores (§2c) → bridges (§2d) → minimum
features (§4) → bed contact, warp, seam (§5) → gate with numbers (§6) → `check_solid`.
Whatever you skipped is reported as a trade-off, not dropped.

Sources: Prusa KB (overhangs, bridging, elephant's foot, teardrops); Protolabs/Hubs FDM
guides (min wall/hole/pin/text); Markforged/MatterHackers testing (anisotropy).
