# Pattern: cantilever snap-fit

## When to use

Two parts clip together tool-lessly: a flexing cantilever beam with a hook on the end
engages a mating window/ledge. Use for battery doors, tool-less lids, clip-on covers.
For the pivoting/hinged alternative see `hinge_clearance.md`.

## Key parametric relationships (aliases in `Parameters`)

Cantilever with an end deflection `δ` (the undercut it must clear during assembly):

- **Peak strain** ε ≈ **1.5 · δ · t / L²**  (t = beam thickness at root, L = beam
  length, δ = deflection). Keep ε below the material's allowable strain:
  - PLA: ~**1%** (brittle — prefer longer beams or avoid).
  - PETG: ~**2–3%**.
  - ABS: ~**2–3%**.
  - Nylon (PA): ~**5–8%** (best snap material).
  These are single-assembly allowables for FDM; halve for repeated cycling. Process- and
  print-orientation-dependent — **assume the beam flexes across layer lines, not along
  them** (layer adhesion is the weak axis).
- Rearranged to size the beam: **L ≥ sqrt(1.5 · δ · t / ε_allow)**. Longer + thinner
  beams flex more safely for a given `δ`.
- **Tapered beam** (thickness at tip ≈ 0.5 · root) spreads strain along the length and
  raises the safe deflection ~1.6×; taper toward the hook.
- **Lead-in (insertion) angle** ~**25–30°** for easy assembly; **retention angle**
  **45°** (semi-permanent) up to **90°** (permanent, needs a release feature).
- **Undercut** (hook engagement depth) `δ` typically **0.5–1.5 mm** — it *is* the
  required deflection; don't make it deeper than the beam can safely flex.
- Add a **root fillet ≥ 0.5 · t** — the sharp root is the usual failure point.

## Force to operate (report it in newtons — `design` §4 wants 2–10 N)

- **Deflection force** at the tip of a straight cantilever:
  **P ≈ b · t³ · E · δ / (4 · L³)** (b = beam width, E = flexural modulus: PLA ~3500,
  PETG ~2000, ABS ~2200, PA ~1500 MPa). A 0.5 taper carries the same δ at roughly
  **1.6 × P** — apply that factor when the beam is tapered.
- **Insertion force** over the lead-in: **F_ins = P · (µ + tan α) / (1 − µ · tan α)**,
  µ ≈ **0.3** for printed plastic on plastic, α = the lead-in angle (25–30°). Retention
  force uses the same expression with the retention angle.
- Too high → **lengthen or thin the beam** (P falls with L³, rises with t³); do not cut
  the undercut, which is the engagement you are relying on.

## FreeCAD build recipe

1. Aliases: `BeamL`, `BeamT`, `BeamW`, `Undercut`, `LeadAngle` (=30), `RetainAngle`,
   `RootFillet` (=`0.5*BeamT`).
2. Sketch the beam profile (length `BeamL` × thickness `BeamT`) on a datum plane
   attached to the wall → **Pad** to `BeamW`.
3. Sketch the hook profile at the free end (lead-in `LeadAngle`, retention
   `RetainAngle`, projection `Undercut`) → **Pad**/**Pocket** to form the barb.
4. **Fillet** the root with `RootFillet`.
5. In the mating part, cut the **window/ledge** the hook catches, offset by the
   assembly clearance (see `tolerances`), driven by the same `Undercut`.

## Pitfalls

- **PLA snaps** — brittle; if PLA is mandatory, lengthen the beam and cut ε to ~0.5%.
- Sharp root (no fillet) → cracks on first flex.
- Beam printed so it flexes **along** layer lines → delaminates; orient the flex across
  layers or note the constraint.
- Undercut deeper than the beam can flex → part won't assemble or beam yields.

## Verify

- Compute ε from the modelled `δ`, `t`, `L` and confirm ε ≤ material allowable — state
  the number in the report.
- `measure` undercut, beam length/thickness, lead-in and retention angles.
- `interference_check` hook vs mating window: engages with the intended catch, clears
  during insertion.
- `check_solid` on both parts.
