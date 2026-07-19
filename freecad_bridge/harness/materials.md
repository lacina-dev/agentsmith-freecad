# Playbook: FDM material selection and properties

Apply whenever the part will actually be manufactured — **pick and state the material
early**, before sizing, because it sets allowable stress, temperature limit, wall
minimums and orientation strategy. Mandatory for 3D-printed or load-bearing parts.
Numbers below are **indicative datasheet-level values and are print-dependent**
(brand, moisture, temperature, layer height, speed) — cite them as ballpark, confirm a
critical design against the real filament's datasheet.

## 1. Filament property table (bulk / well-printed, indicative)

Tensile = ultimate tensile strength; Flex E = flexural modulus; Elong = elongation at
break; Tg/HDT = glass-transition / heat-deflection (the practical "goes soft" temp).

| Material | Tensile (MPa) | Flex E (GPa) | Elong (%) | Tg/HDT (°C) | ρ (g/cm³) | Shrink/warp | Layer adhesion | UV | Typical use |
|---|---|---|---|---|---|---|---|---|---|
| **PLA** | 50–65 | 3.0–4.0 | 3–7 | Tg ~60 / HDT ~55 | 1.24 | very low / low | good | poor | prototypes, jigs, display, indoor low-load |
| **PETG** | 45–55 | 1.5–2.1 | 5–20 | Tg ~80 / HDT ~70 | 1.27 | low / low–med | moderate | good | brackets, mounts, watertight, general outdoor |
| **ABS** | 35–45 | 2.0–2.5 | 5–20 | Tg ~105 / HDT ~90 | 1.05 | high / high | moderate | fair | enclosures, automotive interior, post-processable |
| **ASA** | 40–50 | 2.0–2.6 | 6–20 | Tg ~105 / HDT ~95 | 1.07 | high / high | moderate | excellent | outdoor parts, UV-exposed, automotive exterior |
| **TPU ~95A** | 25–40 | 0.02–0.08 | 300–600 | Tg soft / ~60 | 1.20 | low / low | good | good | flexible, gaskets, bumpers, grips, straps |
| **PC** | 55–70 | 2.0–2.4 | 5–15 | Tg ~145 / HDT ~110+ | 1.20 | high / high | moderate | fair–good | high-temp, high-impact, load-bearing, optical |
| **PA (nylon)** | 45–70 | 1.0–2.0 | 20–100 | Tg ~50–70 / HDT ~80–160 | 1.02–1.14 | med / med (hygroscopic) | very good | fair | living hinges, gears, wear, tough functional |
| **PA-CF** | 80–110 | 4.0–7.0 | 2–6 | HDT ~150+ | 1.10–1.20 | low–med / low | good | fair | stiff structural, gears, brackets, fixtures |

Notes: values are for a single-material print at decent settings. **Moisture wrecks PA
and PC** (dry before printing); **CF grades are abrasive** (hardened nozzle). TPU
strength is meaningless without its huge elongation — it is chosen for flex, not
stiffness.

Unit conversion for the bridge: `mass_properties` expects **density in kg/m³** —
multiply the g/cm³ column by 1000 (PETG 1.27 g/cm³ → `"density": 1270`). Passing the
g/mm³ value (0.00127) silently yields a mass ~10⁶× too small.

## 2. Anisotropy knockdown (the single most important printed-strength rule)

Printed parts are **weakest across the layer lines** (interlayer adhesion). Z-direction
tensile strength is typically only a **fraction of XY**:

| Material | Z / XY tensile ratio (typical) |
|---|---|
| PA (nylon) | ~0.7–0.9 (best) |
| PLA | ~0.5–0.7 |
| PETG | ~0.4–0.6 |
| PC | ~0.4–0.6 |
| ABS/ASA | ~0.3–0.5 (worst of the rigids) |

Rules:
- **Design orientation so principal (tensile/bending) loads lie in-plane (XY)** — never
  let peak tension pull layers apart.
- When a load path unavoidably **crosses layers**, apply the Z/XY ratio as a
  **knockdown factor on allowable stress**: `σ_allow,Z = ratio · σ_allow,XY`.
- Walls/perimeters carry load far better than infill — see `print3d.md` for the
  perimeter-vs-infill and orientation strategy.

## 3. Creep and temperature warnings

- **PLA creeps under sustained load** (deforms slowly at room temp) and **sags above
  ~50 °C** — never for anything hot, structural-under-constant-load, in a car, or in
  direct sun. A PLA bracket that holds today can droop over weeks.
- For **outdoor / hot-car / near-motor / sustained-load** parts use **PETG, ASA, PC**
  (ASA for UV, PC for heat+impact). ABS for heat if UV isn't a factor.
- All thermoplastics lose strength/stiffness as they approach Tg — derate for service
  temperature, don't design at the room-temp number for a hot part.

## 4. Decision recipe

1. **Function → constraints:** peak load & direction, service temperature, outdoor/UV,
   need to flex, food/skin contact, wear/abrasion, impact.
2. **Shortlist by dominant constraint:**
   - Indoor, low-load, easy print, cheap → **PLA**.
   - General purpose, tough, watertight, mild outdoor → **PETG**.
   - Outdoor/UV, dimensional at heat → **ASA** (or ABS indoors).
   - Hot + high-impact + structural → **PC**.
   - Flexible/gasket/grip → **TPU**.
   - Tough, fatigue, living hinge, gears, snaps → **PA**.
   - Stiff structural fixture/bracket → **PA-CF** (or PC).
3. **State the chosen material and its allowable stress in the report**, derived as:
   `σ_allow = σ_material / FoS × (Z/XY knockdown if load crosses layers)`
   — FoS and sizing per `mechanics.md`, load-case per `strength.md`.
4. **Record it as a driver when it drives geometry:** put `Material`, `TensileMPa`,
   `AllowableMPa` (and the knockdown used) as notes/aliases in the `Parameters`
   Spreadsheet so wall thicknesses derived from strength stay auditable.

## Sources (corroborated across multiple references)

- Filament datasheets and comparison references (Prusa, Bambu, Polymaker material
  pages; Simplify3D / MatterHackers material guides) for strength/modulus/Tg/density.
- Anisotropy (Z vs XY) and layer-adhesion data: Prusa Knowledge Base mechanical
  properties, Markforged/MatterHackers tensile testing.
- Creep/temperature behaviour: general thermoplastics engineering guidance; PLA creep
  and low HDT widely documented.
