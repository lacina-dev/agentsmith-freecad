# Playbook: sheet-metal parts

Goal: parts that can be cut and bent from a single gauge of sheet, unfold flat, and
respect brake/laser limits. Use FreeCAD's SheetMetal workbench where available so the
flat pattern stays valid.

## 1. Material and gauge

- Choose one **uniform sheet thickness (T)** for the whole part; all features scale from
  T. Note material and gauge in the `Parameters` spreadsheet.

## 2. Bends

- **Minimum inside bend radius ≈ material thickness (R ≥ T)**; tighter radii crack or
  need special tooling. Use a consistent bend radius across the part.
- **K-factor ≈ 0.4** (typ. range 0.3–0.5) for bend-allowance / flat-pattern length.
- Keep the number of distinct bend radii small (one brake setup per radius).

## 3. Relief and holes

- Add **bend relief** at the ends of partial bends: depth ≥ R + T, width ≥ T, to avoid
  tearing.
- Keep **holes/slots away from bends: distance D ≥ 2T + R** (from hole edge to bend
  line) so they don't distort.
- Keep holes/features away from edges by ≥ ~2T.

## 4. Manufacturability

- Ensure the part **unfolds to a flat pattern** without overlaps; verify the flat is
  producible on the available cutter.
- Prefer bends over welds where possible; call out any welds/hardware separately.

## 5. Verify & deliver

- Confirm uniform thickness, R ≥ T on every bend, relief present, hole-to-bend ≥ 2T+R.
- Confirm the flat pattern generates cleanly.
- Report thickness, bend radius, K-factor used, and confirm the flat pattern is valid.
