# Playbook: designing for strength (load cases, allowables, margin of safety)

Apply whenever the part carries real load. This playbook owns the **engineering
discipline** — identify the load case, choose the right failure basis, prove a positive
**margin of safety with numbers**. It **delegates the details**:

- **Material allowables and anisotropy** → `materials.md` (strength/modulus tables,
  Z-vs-XY knockdown, creep/temperature).
- **Sizing math** (σ, δ, section modulus, buckling, bearing, pull-out, torque) →
  `mechanics.md`.
- **Print orientation and layer-direction strategy** → `print3d.md`.

Don't restate those tables here; pull the numbers from them and do the reasoning.

## 1. Define the load case first (before sizing anything)

- What loads act, in which **direction**, and how big — use the **worst case, not
  nominal**? Trace the **load path** from where force enters to the supports.
- **Static** (steady), **dynamic/cyclic** (fatigue), or **impact/shock**? Cyclic and
  impact are far more demanding than a steady pull, and drive a higher safety factor.
- Required **life** (cycles) and **environment** (temperature, UV, moisture, chemicals)
  — these knock strength/stiffness down, especially for plastics (see `materials.md`).
- Is **stiffness/deflection** a requirement too, not just strength? Deflection is
  governed by `E·I`, independent of strength — a part can be strong but too bendy.

## 2. Choose the failure basis and allowable stress

- **Ductile** metals/plastics: design against **yield** `Re` (permanent deformation is
  failure).
- **Brittle** behaviour (cast iron, ceramics, **PLA under impact**): design against
  **ultimate** `Rm`, with a larger factor — it fails without warning.
- **Allowable stress** = material strength ÷ factor of safety, **then apply the
  anisotropy knockdown from `materials.md` if the load crosses layer lines**. Keep
  working stress `σ ≤ σ_allow`.

### Factor-of-safety basis (which failure mode governs)

| Material | Loading | Basis | Typical FoS |
|---|---|---|---|
| Ductile | Static | Yield `Re` | 1.5 – 2.0 |
| Ductile | Dynamic/fluctuating | Yield `Re` | 2 – 3 |
| Ductile | Fatigue | Endurance limit | 1.5 – 2.5 |
| Brittle | Static | Ultimate `Rm` | 3 – 4 (up to 8) |
| Brittle | Dynamic/fatigue | Ultimate `Rm` | 4 – 8 |

Raise the factor with uncertainty, load scatter, or serious consequences of failure;
lower only with test/analysis backing. (`mechanics.md` gives the everyday shorthand:
~2 static, ~3–4 dynamic/impact/human-carried.)

## 3. Size, compare, iterate

- Run the governing check(s) from `mechanics.md` — bending `σ = M·c/I`, axial `F/A`,
  buckling, bearing, pull-out — on the **actual section** (`measure` / `model_digest`).
- **Fillet every internal corner/step/hole** — sharp corners multiply stress `Kt ≈ 2–3`
  (`mechanics.md` §2). This is the cheapest strength win and decisive under cyclic load.
- Add material where stress is high (near supports/loads), remove it where low; prefer
  **ribs, gussets, I/box sections** over solid mass (`rib_gusset.md`).
- **Fatigue note:** cyclic stress fails parts well below static strength. Steel has an
  endurance limit ≈ 0.5·Rm; aluminium and most plastics have no true endurance limit —
  use a fatigue strength at the target life and a higher FoS. Smooth surfaces and
  fillets extend fatigue life.

## 4. FDM-specific strength (delegated, but never skip)

- Printed parts are **anisotropic and weaker than bulk** — the load path must run
  **in-plane (XY)**; apply the **Z/XY knockdown** from `materials.md` where it can't.
- **Walls/perimeters carry the load, not infill** — 3–4+ perimeters for structural
  parts; see `print3d.md` §2 for the perimeter/infill/orientation strategy.
- Combine with `print3d.md` overhang/orientation rules so the strong design is also
  printable — the two constraints must be solved together.

## 5. Report the margin of safety (mandatory, with the arithmetic)

State, with numbers:

- The **load case** (magnitude, direction, static/dynamic/impact, assumptions made).
- The **governing check** (bending / axial / buckling / bearing / fatigue / pull-out).
- **Working stress** `σ` (from the actual section) and **allowable stress** `σ_allow`
  (material ÷ FoS × knockdown — cite where each number came from).
- **Margin of safety** `MoS = σ_allow / σ − 1`. **`MoS ≥ 0` to pass**; if negative,
  iterate (thicker section, taller `h`, fillet, rib, reorient, tougher material) and
  re-report.
- Put `Material`, `Re`/`Rm`/`E`, chosen `FoS`, load, `σ_allow` and the driven
  cross-sections into the `Parameters` Spreadsheet so the sizing stays editable and
  auditable.

## Bridge tie-ins

- `mass_properties` → self-weight / centre of mass for the load case.
- `measure` / `model_digest` → the real section dimensions feeding the stress formula.
- `cross_section` → confirm the true load-bearing wall/section area.

## Sources (corroborated across multiple references)

- Machine-design FoS and yield-vs-ultimate / static-dynamic-fatigue guidance
  (Shigley-type tables).
- Beam/section/buckling formulas per `mechanics.md`; material allowables and FDM
  anisotropy per `materials.md`.
