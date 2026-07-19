# Playbook: mechanics / dimensioning (size it with numbers, not vibes)

Apply whenever the part **carries load, transmits force/torque, or must not deflect too
much**. Do the arithmetic in the report: worst-case load → stress/deflection → compare
to the allowable from `materials.md` → iterate parameters. A part sized by feel is a
guess; a part sized by these formulas is an engineering decision you can defend.

All formulas are first-order (linear-elastic, small deflection). They are for **sizing
and sanity**, not certification — for critical/complex geometry, back them with FEA.

## 1. Beam bending (the workhorse)

For a beam of length `L`, second moment of area `I`, distance to outer fibre `c`,
modulus `E`, under bending moment `M`:

- **Bending stress:** `σ = M·c / I`  (peak at the outer fibre, at max-moment section).
- **Rectangular section** (width `b`, height `h` in the bending direction):
  - `I = b·h³ / 12`, `c = h/2`, so section modulus `Z = I/c = b·h² / 6`.
  - Round bar Ø`d`: `I = π·d⁴/64`, `Z = π·d³/32`.

**Common load cases** (force `F`, distributed load `w` = force per length):

| Case | Max moment `M` | Max deflection `δ` |
|---|---|---|
| Cantilever, end point load | `F·L` (at wall) | `F·L³ / (3·E·I)` |
| Cantilever, uniform load | `w·L²/2` | `w·L⁴ / (8·E·I)` |
| Simply supported, centre load | `F·L/4` | `F·L³ / (48·E·I)` |
| Simply supported, uniform load | `w·L²/8` | `5·w·L⁴ / (384·E·I)` |

**The h³ lever (remember this):** stiffness ∝ `I ∝ h³`. **Double the height → 8× stiffer
and 4× stronger** (`Z ∝ h²`) for the same width. Add depth in the load direction (or a
rib, `rib_gusset.md`) before adding width or bulk. Orient a printed beam so `h` is the
tall in-plane dimension.

## 2. Stress concentration (always fillet)

- A sharp internal corner, step, or hole edge multiplies local stress by
  **`Kt ≈ 1.5–3`** (sharper/tighter → higher; a near-square internal corner is ~3).
- **Effective stress = `Kt · σ_nom`.** Compare *that* to the allowable, not the nominal.
- **Add a fillet** at every internal corner, step and hole edge: a radius of ~0.2–0.5×
  the local thickness drops `Kt` toward ~1.2–1.5. Cheapest strength win there is, and
  decisive under cyclic/impact load.

## 3. Axial, bearing, thread/insert pull-out, torque

- **Axial (tension/compression):** `σ = F / A`.
- **Bearing/crush stress** under a pin, washer, screw head or press-fit:
  `σ_bear = F / A_contact` (projected/annular contact area). Keep below the material's
  allowable so the plastic doesn't crush or the screw head doesn't sink in — spread load
  with a washer or larger boss face.
- **Thread / heat-set insert pull-out** ≈ shear over the engaged cylinder:
  `F_pull ≈ τ_allow · π · D · L_engage` (D = thread/insert Ø, L = engagement length).
  Practical rule: **engagement length ≥ 2× screw Ø** in plastic (heat-set insert or
  thread-forming); more for soft materials or high load. Prefer brass heat-set inserts
  over threading into plastic for repeated assembly (`heat_set_insert.md`).
- **Torque → force on a lever arm:** `F = T / r` (r = radius/arm). A handle, gear tooth,
  or bolt circle turns torque into a tangential force; feed that `F` into the bending or
  bearing check above. Torque on a shaft gives shear `τ = T·c / J`, `J = π·d⁴/32` for a
  round shaft.

## 4. Safety factors (FoS)

- **Static, well-known load, well-known material:** FoS ≈ **2**.
- **Dynamic / cyclic / impact / human-carried / uncertain load:** FoS ≈ **3–4** (higher
  if failure is dangerous or loads are poorly known).
- **Allowable stress** `σ_allow = σ_material / FoS`, with the **anisotropy knockdown from
  `materials.md`** when the load crosses layer lines: `σ_allow ← σ_allow × (Z/XY ratio)`.
- Brittle behaviour (PLA in impact) → design against ultimate with a larger factor; see
  `strength.md` for the yield-vs-ultimate and load-case discipline.

## 5. Buckling and thin walls

- **Slender columns / thin walls in compression fail by buckling long before they reach
  crushing stress.** Euler critical load: `P_cr = π²·E·I / (K·L)²` (K ≈ 1 pinned–pinned,
  0.5 fixed–fixed, 2 fixed–free). If `P_cr` is near the working load, the column is
  buckling-governed — thicken, shorten, or brace it.
- Thin panels under edge/bending load: **stiffen with ribs** (`rib_gusset.md`) rather
  than adding thickness — far more `I` per gram, and avoids thick-section print/mould
  problems. A shallow rib grid beats a thick flat plate.

## 6. Workflow (show the arithmetic)

1. **Identify the load path** — where force enters, how it travels to the supports.
2. **Worst-case load, with numbers** — state every assumption (mass × g, a stated hand
   force, a dynamic/impact multiplier). Use `mass_properties` for the part's own weight.
3. **Pick the governing check** — bending, axial, bearing, buckling, pull-out.
4. **Compute** `σ` (and `δ` if stiffness matters) from the **actual section dimensions**
   — read them with `measure` / `model_digest`, not from intent.
5. **Compare vs allowable** from `materials.md` (with FoS and knockdown). Report the
   **margin of safety** `MoS = allowable/working − 1 ≥ 0` (see `strength.md`).
6. **Iterate parameters** — bump `h`, add a fillet/rib, change orientation or material —
   and re-check. Drive the section dims from `Parameters` aliases so the iteration is
   editable.

## Bridge tie-ins

- `mass_properties` → self-weight and centre of mass for the load case.
- `measure` / `model_digest` → the **actual** section `b`, `h`, `d`, fillet radii to feed
  the formulas (never guess the section from a render).
- `cross_section` → confirm the real load-bearing wall/section area.

## Sources (corroborated across multiple references)

- Standard beam formulas (Roark / Shigley-type mechanics-of-materials tables) for
  moment, deflection, section modulus, Euler buckling, torsion.
- Stress-concentration `Kt` ranges from standard `Kt` charts (Peterson-type).
- Plastic insert/thread pull-out and bearing-stress rules of thumb from fastener and
  insert manufacturer guidance.
