# Playbook: real fasteners and gears (Fasteners + Gears workbenches)

This project is not only about 3D printing. Many models are complete assemblies
that happen to get printed — and some never do. For those, **the actual screws
belong in the model**: they show that the fastener fits, that the head clears, that
the thread reaches, and they make the assembly legible to a human looking at it.

Both add-ons are installed and both work **without the GUI**, so you can drive them
from `execute_python` through the bridge. The calls below were verified on this
machine; use them as written rather than guessing at an API.

## When to place a real fastener, and when just the hole

| Situation | What to model |
|---|---|
| Complex assembly, several parts, someone must understand how it goes together | Real fastener objects **and** the holes |
| Load path or clearance in doubt (does the head foul? does the thread reach?) | Real fastener — it answers the question geometrically |
| Single printed part with mounting holes | Holes only; a screw object adds nothing |
| Part shipped to someone who may not have the add-on | Holes natively; keep fasteners in a separate, clearly named group |

The one real caveat, and it is a note rather than a prohibition: a document whose
screws are `Part::FeaturePython` objects from an add-on **needs that add-on to
rebuild them**. Opened without it the shapes survive but stop being parametric. So:
model holes, counterbores and nut pockets **natively** — those are the part — and
let the fastener objects represent the hardware.

## Fasteners: verified calls

```python
import FastenersCmd
screw = doc.addObject("Part::FeaturePython", "Screw")
FastenersCmd.FSScrewObject(screw, "ISO4762", None)   # socket-head cap screw
screw.Diameter = "M5"
screw.Length = "16"
doc.recompute()
# ISO4762 M5x16 -> head Ø9.2 mm, 21 mm overall, 550 mm3
```

Useful types (570 standards are available; `FastenerBase.FsTitles` lists them):

| Type string | What it is |
|---|---|
| `ISO4762` | socket-head cap screw (the default choice) |
| `ISO7046` / `ISO14583` | countersunk |
| `ISO4032` | hex nut |
| `ISO7089` | washer |
| `ThreadedRod` | **threaded rod — the reinforcement for long printed parts** |
| `ScrewTap` | tapped-hole helper |

Threaded rod, which is what turns a weak layer bond into a steel problem:

```python
rod = doc.addObject("Part::FeaturePython", "Rod")
FastenersCmd.FSScrewObject(rod, "ThreadedRod", None)
rod.Diameter = "M5"
rod.Length = "200"      # verified: 200 mm, 3918 mm3
```

Place fasteners by setting `Placement`, and keep them in their own group so the
printable bodies stay separable from the bought hardware. Say in your report which
hardware the user must buy: standard, size, length, count.

## Gears: verified calls

```python
from freecad.gears.features import InvoluteGear
gear = doc.addObject("Part::FeaturePython", "Gear")
InvoluteGear(gear)
gear.num_teeth = 20
gear.module = "2 mm"
gear.height = "6 mm"
doc.recompute()
# z=20, m=2 -> tip diameter 44 mm
```

Available types: `InvoluteGear`, `InternalInvoluteGear`, `InvoluteGearRack`,
`CycloidGear`, `CycloidGearRack`, `BevelGear`, `CrownGear`, `LanternGear`,
`TimingGear`, `TimingGearT`.

### Gear arithmetic you must state before modelling

- **Pitch diameter** d = m · z. **Tip diameter** ≈ m · (z + 2).
- **Centre distance** between two spur gears a = m · (z₁ + z₂) / 2. Model the
  bearing positions from this expression, not from a measured drawing — an
  eyeballed centre distance binds or backlashes.
- **Ratio** i = z₂ / z₁. Say what ratio the design needs and show it comes out.
- Both meshing gears must share the **same module**. This is the single most
  common gear mistake.

### Printed gears specifically

- Fewer than ~17 teeth on a 20° involute undercuts; use profile shift or more teeth.
- Add **backlash** — FDM comes out oversized. Start at 0.1–0.3 mm and expose it as a
  parameter; a gear pair modelled at nominal will not turn.
- Keep the module ≥ 1.5 mm for printed teeth; below that the tooth is smaller than
  the extrusion can resolve.
- Orient so the teeth print in-plane; teeth built up the Z axis shear along layers.
- Gears need a shaft. Decide how the gear is retained on it — grub screw onto a
  flat, D-shaft, or a printed hub with a clamp — before the model is finished. A
  gear that spins freely on its shaft transmits nothing. (See the "Sestavy"
  playbook: what holds it, and what stops it rotating.)

## Verify

- After placing hardware, run `interference_check` — that is exactly what real
  fastener geometry is for.
- Check head and nut clearance against neighbouring walls with `measure`.
- For a gear pair, confirm the centre distance against m·(z₁+z₂)/2 numerically and
  report both numbers.
