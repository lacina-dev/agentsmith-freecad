# Playbook: design, ergonomics and looks (parts people want to touch)

Apply whenever a human sees, holds, operates, inserts, removes or carries the part.
Function first: never override `strength`, `mechanics`, `tolerances`, `print3d` §1 or §4.
Every rule below is a measured number; "it looks fine" is not evidence.

## 1. Edge policy — classify before you fillet

Classify the edges a hand or the eye reaches — silhouette, bed-facing, mating — not all N
edges; list the **classes** and their treatment, and any left sharp and why.

- **Hand edge** (finger/palm reaches it): **R ≥ 2 mm**, **≥ 3 mm** under grip load.
  **Never a bare chamfer** — it makes two arrises where there was one. Nothing with an
  included angle < 90° where a hand reaches: break it **R ≥ 1 mm**.
- **Seen edge** (visible, no contact): **one size across the whole silhouette** —
  **R 1–2 mm** or a **C of the same offset**; fillet vertical edges, chamfer horizontal and
  bed-facing ones (`print3d` §5). The **size** must not vary; the operation may.
- **Bed edge** (down-facing): **chamfer 0.4–0.6 mm × 45°, never a fillet** (`print3d` §5).
- **Internal root**: sized by structure — `mechanics` §2, `strength`.
- **Functional edge** (mating, sealing, datum): sharp except a lead-in (§5) — a fillet
  there destroys the fit you toleranced.

**Fillet last, largest first**: geometry → structural roots → cosmetic breaks. Small
before large makes FreeCAD's fillet fail or leave a knife edge; halve a failing one
rather than deleting it. Ceiling **≤ 0.4 × wall**, **≤ 0.5 × the shortest adjacent edge** —
a 1.6–1.8 mm structural wall (`print3d` §4) caps a fillet at ~0.7 mm, so **a hand edge
needs local material**: where R ≥ 2 mm exceeds the ceiling, thicken the zone (bead, rolled
lip, boss **≥ 2.5 × R**), never shrink the radius. A hand edge under **R 1 mm** is a
DEVIATION, not a compromise.

## 2. Radius family and proportion

- **≤ 3 cosmetic radius values, all `Parameters` aliases** (`Rmain`, `Rsecond =
  Rmain/2`, `Cedge`). Neighbours differ ~2×; **never two radii within 30 %**
  of each other. A fourth value needs a stated reason.
- **Verify**: a fillet on a **straight** edge shows up in `feature_probe` `holes`. Every
  `kind: boss` entry with `angle_deg` **60–200** is a convex break — `radius_mm` in the
  family **±0.05 mm**. `kind: hole` entries are structural roots or functional features:
  list them with the rule that sized them (`mechanics` §2, `strength`, `tolerances`), don't
  force them into the family. `angle_deg` is the **summed** sweep of a coaxial group —
  outside the window means "check by hand", not "pass". Tori (fillet on a curved edge) and
  cones (chamfers) never appear: use `cross_section`, and ignore functional bores.
- **Constant wall round a corner: outside R = inside r + wall**, **r = 0.25–0.6 × wall**
  (0.5 typical) → outside ≈ 1.5 × wall. Prove with `cross_section` at the corner.
- **Two freely chosen, visible dimensions** are equal (one alias) or differ by **≥ 15 %** —
  50 beside 53 reads as a mistake. Dimensions fixed by a fit, a standard or a fastener
  (`tolerances`, `fasteners`) are exempt and are never adjusted for looks.
- **Align to shared datums**: `feature_probe` `axis_point` coordinates that should be
  collinear agree **≤ 0.05 mm**; equal spacings are exactly equal, not 19.8 / 20.1.
- **≤ 2 distinct wall thicknesses** (nominal + one, for a stated reason); ribs per
  `enclosures`. Cite each from `cross_section`.
- **No bare slab**: a visible flat face over ~**1500 mm²** (`feature_probe`
  `planes[].area_mm2`) gets a border chamfer ≥ 1 mm, a recess ≥ 0.5 mm, or a relief —
  also cuts warp.

## 3. Hand, grip and access numbers (state each against its range)

- **Power grip Ø 30–40 mm** (33 mm optimum; ~35 female, ~40 male; ≤ 50 pull-only).
  **Precision/pinch grip Ø 8–16 mm.** Non-round: circumscribed Ø in the same window,
  long edges R ≥ 3 mm. **No load-bearing grip surface under 10 mm wide.**
- **Grip length ≥ 100 mm, 120 preferred**, +15–25 mm gloved. **Two-handled squeeze
  span 65–90 mm** (>100 excludes small hands).
- **Openings** (95th-percentile male, +10 mm gloved): four fingers through a bail
  **≥ 100 × 40 mm**; one finger hooked **Ø ≥ 25 mm** (20 bare minimum); flat hand into a
  cavity **≥ 100 × 45 mm**. Clearance round a bare-finger grip **≥ 25 mm**, gloved
  **≥ 32 mm**, firm grip **≥ 64 mm**.
- **Thumb recess / push pad ≥ 25 mm wide (30 preferred) × 8–12 mm deep**, floor radius
  ≥ 10 mm — a thumb is 23–25 mm across. **Nail pry gap 2–3 mm × ≥ 15 mm wide.**
- **`mass_properties` ≤ 1.4 kg** carried one-handed, **≤ 2.3 kg** lifted above shoulder,
  **≤ 0.4 kg** precision-manipulated. Over → hollow, rib, or add a second grip.
- **Measure them**: round grip Ø → `feature_probe` `holes[].diameter_mm`; non-round grip,
  recess width/depth, opening → `cross_section` there; extents → `model_digest` bbox.
  `measure` gives the distance between **two objects**; it cannot size one body.

## 4. Forces to operate (report them in newtons)

- **Fingertip button / clip release 2–10 N** (3–6 comfortable, >15 N two-fingered, >25 N
  nobody repeats). **Lid or press fit by hand 10–30 N**; >50 N needs a tool.
  Ceiling **≤ 1/3 of the weakest user's maximum**.
- **Compute snap and insertion force** from `patterns/snap_fit_cantilever.md` ("Force to
  operate"; patterns are not auto-injected — `cat` it) and land it in that band; if high,
  lengthen or thin the beam rather than cutting hook engagement.

## 5. Insertion, removal, stability

- **Lead-in on both sides of every insertion**: **0.5–1 mm × 30–45°** on the male nose
  *and* the female mouth; on a blind slide **2–3 × the clearance** long.
- **Removed more often than weekly → a removal feature**: thumb notch (§3), a lip
  overhanging **≥ 5 mm**, or a 2–3 mm pry gap — named, with dimensions.
- **Locate, then fasten, then bottom out**: a pin, rib or key engages **2–3 mm before**
  screws or snaps take load (offset from `cross_section`, or two `feature_probe`
  `axis_point`/plane `center` values differenced); the part seats on a flat face **≥ 2 ×
  the mating wall**, never on a fillet, chamfer, screw head or thread end.
- **Block the wrong way**: if it can be fitted backwards and that is wrong, add
  **≥ 1.5 mm** of asymmetry (offset pin, cut corner, unequal spacing); prove it with
  `interference_check` in the wrong pose — any scratch solid is deleted in the same step
  (`verify`).
- **Contact surfaces**: stand on **3 feet** (three never rock), **1–3 mm high**,
  R ≥ 1 mm, inset ≥ 3 mm; 4 only if the load path demands it, coplanar within 0.1 mm.
  A face clamping a finished object (phone, glass, furniture) gets **R ≥ 1 mm on every
  contact edge** and 3–4 pads with 0.5 mm relief, not full-face contact.
- **Prove it will not tip**: in the use pose, from `mass_properties` CoM and the footprint,
  survive a **10° tilt any direction** — **CoM height ≤ 5.7 × the shortest horizontal
  CoM-to-footprint-edge distance**. Footprint = the bed-facing extent of the `model_digest`
  bbox (for a shaped base take the worst CoM-to-outer-wire distance of the base sketch and
  say which you used); `feature_probe` `planes[0]` only names the contact face. Report the
  angle **with the load in it** (held mass at its CoM, size from `reference-dimensions`);
  fails → wider base, lower mass, or a modelled fixing.

## 6. Surface, orientation and text

- **Aim the good surface at the user**: top faces (solid/ironed) best, vertical walls
  acceptable, down-facing/supported worst. Name the cosmetic face and justify its
  orientation — but strength and supports win the tie (`print3d` §1); cosmetics bought
  with strength is a reported trade-off.
- **Kill shallow slopes on cosmetic faces**: **~5–25° from horizontal** stair-steps
  visibly — make it 0°, steeper than **30°**, or say you accept it. `print_readiness` sees
  only **down**-facing faces, so check the cosmetic plane's `feature_probe`
  `planes[].normal`: **|dot(normal, up)| outside 0.09–0.42**, or cite the driving
  sketch/taper angle from `Parameters`. Keep the seam (`print3d` §5) off cosmetic and grip
  faces — give it a corner or a 0.3–0.5 × 0.6–1 mm groove.
- **Text**: minima and depths per `print3d` §4; on a face the user must **read**, raise cap
  height to **≥ 4 mm** and stroke to **≥ 0.8 mm**, sans-serif. **Deboss top faces, emboss
  vertical walls**, never down-facing; glyphs **≥ 2 mm** from an edge or fillet tangent;
  reads in the use pose. Verify the depth with `cross_section`, not by eye.
- **Label what the user needs later**: `UP`/`FRONT` where it can be fitted wrong,
  matching marks on a set, the variant a re-printable part was built for.

## 7. Design review — before you declare done

Cite a measured value for each.

1. Every edge **class** treated, the sharp ones justified (§1); radius family ≤ 3
   aliased values, confirmed from `feature_probe` ±0.05 mm (§2).
2. Grip / access / thumb dimensions and mass inside the §3 ranges.
3. Operating forces in newtons (§4); tilt angle **≥ 10° loaded** (§5).
4. Cosmetic face named and justified; text depth measured (§6).
5. **Render the use pose** (the axonometric `verify` requires, plus the touched face) and
   name anything accidental — a part that passes every dimension and still looks extruded
   is a defect to fix, not a footnote.

Sources: grip Ø — Kong & Lowe 2005, Garneau & Hall 2003; length/span/carry mass — CCOHS
*Hand Tool Ergonomics*; clearances — MIL-STD-1472 F/G, EN 547-2; 10° tilt — IEC 62368-1.
