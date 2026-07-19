# Pattern: counterbore / countersink for cap- and flat-head screws

## When to use

A screw must sit **flush or recessed** in the part it passes through: a **counterbore**
for a socket-head cap screw (DIN 912 / ISO 4762) or a **countersink** for a flat/
countersunk head (DIN 7991 / ISO 10642). This is the **through/clearance** side of a
joint — the boss/insert side is `threaded_boss.md` / `heat_set_insert.md`.

## Key parametric relationships (aliases in `Parameters`)

**Clearance hole** (screw passes through freely) — pick the series to taste:

| Screw | Close | Normal | Loose |
|-------|-------|--------|-------|
| M3 | 3.2 | 3.4 | 3.6 |
| M4 | 4.3 | 4.5 | 4.8 |
| M5 | 5.3 | 5.5 | 5.8 |
| M6 | 6.4 | 6.6 | 7.0 |

**Counterbore for DIN 912 socket-head cap screw** (bore Ø ≈ head Ø + ~0.4 mm slip;
depth ≥ head height so the head sits at/below the surface):

| Screw | Head Ø | C'bore Ø | Head height ≈ min c'bore depth |
|-------|--------|----------|-------------------------------|
| M3 | 5.5 | 6.0 | 3.0 |
| M4 | 7.0 | 8.0 | 4.0 |
| M5 | 8.5 | 9.5 | 5.0 |
| M6 | 10.0 | 11.0 | 6.0 |

- Make the counterbore **depth ≥ head height** (add a hair more to guarantee flush/
  below); for a socket driver, ensure the bore clears the key.

**Countersink for DIN 7991 flat-head:** included angle **90°**. Sink diameter at the
surface ≈ the head Ø for that size (e.g. M3 ≈ 6.0, M4 ≈ 8.0, M5 ≈ 9.4, M6 ≈ 11.3 mm).
Cut the 90° cone so the head finishes flush; keep enough wall below the cone.

- Values are widely-cited nominal; **the head Ø/height come from the fastener standard
  or datasheet** (see `research`) — treat these as defaults and confirm for the exact
  screw.

## FreeCAD build recipe

1. Aliases: `ScrewD`, `ClearD` (series above), `HeadD`, `CboreD` (=`HeadD + 0.4`),
   `CboreDepth` (=`HeadHeight + 0.2`) or `CsinkAngle` (=90).
2. Sketch Ø`ClearD` on the entry face/datum, on the screw axis → **Pocket** through.
3. Counterbore: sketch Ø`CboreD` concentric → **Pocket** to `CboreDepth` from the
   entry face. Countersink: use a **chamfer** (angle 45° → 90° included) at the mouth,
   or pocket a 90° cone sized to `HeadD`.
4. **Array** to the bolt pattern from the same datums so all holes track together.

## Pitfalls

- Counterbore **shallower than the head** → head stands proud, part won't seat flat.
- Clearance hole too tight (used a tap-drill size) → screw binds, can't align parts.
- Countersink angle wrong (82° UNC vs **90°** metric DIN 7991) → head sits proud or
  bottoms on the edge.
- Bore/countersink not concentric with the clearance hole, or referenced to a face
  name instead of the axis datum → misalignment on edits.

## Verify

- `measure` clearance Ø, counterbore Ø/depth (or countersink angle & sink Ø) against
  the aliases and the screw's head spec.
- `cross_section` through the hole axis: the head profile fits inside the recess and
  finishes flush or below the surface.
- `interference_check`: the modelled screw head sits within the recess; shaft clears.
- `check_solid`: still one watertight solid after the cuts.
