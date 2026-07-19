# Pattern: press-fit bearing pocket

## When to use

A printed part must hold a **standard rolling-element bearing** (e.g. 608, 6000-series,
or a small ball bearing) so a shaft can spin with low friction. The pocket grips the
bearing's **outer race** by interference and seats it against a shoulder. Worked example
below is the ubiquitous **608** bearing: **OD 22 mm × bore 8 mm × width 7 mm**.

## Key parametric relationships (aliases in `Parameters`)

Let `BearingOD`, `BearingBore`, `BearingW` = the bearing's datasheet dimensions
(608 → 22 / 8 / 7). Only ever grip the **outer race**; the inner race and shaft must
spin free.

- **Interference (press) on the OD `PressFit` = ~0.05–0.15 mm on diameter** for FDM
  (i.e. pocket Ø = `BearingOD − PressFit`). FDM holes print undersize and vary, so this
  is a starting point — **print a test pocket and tune**; too tight cracks the printed
  boss or crushes the race, too loose spins the outer race.
  - 608 example: pocket Ø ≈ **21.85–21.95 mm** (`22 − 0.05…0.15`).
- **Seat shoulder** on the **outer race only:** a shoulder/lip of radial width
  **~1–1.5 mm** at the pocket bottom. Its **inner** Ø must clear the inner race and any
  seal shield — make the shoulder bore **larger than the bearing bore + inner-race
  width**, so nothing touches the spinning inner race.
  - 608: shoulder bore ≈ **12–14 mm** (clears the ~12 mm inner-race shield), leaving a
    ~4–5 mm annular ledge under the 22 mm outer race.
- **Lead-in chamfer** at the pocket mouth **~0.5–1 mm × 45°** to start the bearing square
  and let the press begin without shaving the boss.
- **Boss wall around the bearing ≥ ~2 mm** (≥ 3–4 perimeters) so the interference doesn't
  split it — `PocketBossOD ≥ BearingOD + 4`.
- **Pocket depth = `BearingW`** (flush) or `BearingW + clearance` for a through design;
  **don't trap both faces** unless intended.

## FreeCAD build recipe

1. Aliases: `BearingOD` (=22), `BearingBore` (=8), `BearingW` (=7), `PressFit` (=0.10),
   `PocketD` (=`BearingOD − PressFit`), `ShoulderBore` (=13), `LeadIn` (=0.75),
   `BossOD` (=`BearingOD + 4`).
2. Sketch Ø`BossOD` on the mounting datum → **Pad** to `BearingW + shoulder + backwall`.
3. Sketch Ø`PocketD` concentric → **Pocket** to depth `BearingW` (the press bore).
4. Sketch Ø`ShoulderBore` concentric → **Pocket** deeper (the clearance bore behind the
   bearing) → forms the outer-race shoulder as the step between `PocketD` and
   `ShoulderBore`.
5. **Chamfer** the pocket mouth `LeadIn` × 45°.
6. If a shaft passes through, ensure the through-bore clears `BearingBore` + the shaft —
   never let the printed part rub the inner race.

## Pitfalls

- **Shoulder touches the inner race** → drags the "bearing", kills the whole point.
  The shoulder must contact the **outer race only**.
- **Preloading/pinching the inner race** axially → binds rotation. Leave the inner race
  and shaft axially free.
- **Interference too tight** → cracks the boss or brinells the race; **too loose** → the
  outer race spins in the pocket (add a dab of retainer or reprint tighter).
- Pocket sized to nominal OD without accounting for FDM undersize → actually too tight;
  measure a test print.
- Pocket referenced to a face, not the shaft-axis datum → edits/arrays break.

## Verify

- `measure` pocket Ø vs `BearingOD − PressFit`, shoulder bore, depth, chamfer against the
  aliases and the bearing datasheet.
- `cross_section` through the pocket axis → confirm the shoulder engages the **outer**
  race only (shoulder bore clears the inner race), boss wall ≥ 2 mm, depth = `BearingW`.
- `check_solid` → single watertight boss.
- Confirm the shaft/through-bore does not contact the inner race region
  (`interference_check` if the shaft is modelled).
