# Playbook: dimensions of everyday objects

For parts that hold, fit or mount to something that already exists. The numbers are
**ranges, not constants** — most of these objects vary by market and brand by tens of
millimetres, which is exactly enough to turn a holder into a decoration.

## How to use this table

1. Find the object; if it is not here, look it up (`research.md`) — do not guess
   because it feels familiar.
2. **Design for the top of the range**, then add clearance — a holder built for the
   smallest variant is broken for everyone else.
3. Write the chosen value and the variant into `Parameters`, and say in the report which
   variant you designed for.
4. Cross-check anything critical against a second source; where sources here disagreed,
   the disagreement is left in rather than averaged away.

**Handling allowance** — what the user drops in and pulls out by hand: **1–3 mm per
side**, more where they cannot see. That is not a fit; fits live in `tolerances.md` §4.

## Paper and household

| Object | Length / height | Diameter / width | Notes |
|---|---|---|---|
| Kitchen towel roll | **230–280 mm** | Ø 105–150 mm | CZ/Zewa ~230, DE ~260, US 11″ = 279. Core Ø 38–45 mm. **This spread is why the table exists.** |
| Toilet roll | 95–112 mm | Ø 100–130 mm | Core Ø 40–45 mm; "jumbo" household rolls reach 130 mm |
| A4 sheet | 297 mm | 210 mm | ISO 216, exact |
| A5 sheet | 210 mm | 148 mm | ISO 216, exact |
| Standard brick (CZ CP) | 290 mm | 140 × 65 mm | |

## Fasteners and hardware

| Object | Key dimension | Notes |
|---|---|---|
| M3 / M4 / M5 / M6 screw | clearance hole Ø 3.4 / 4.5 / 5.5 / 6.6 mm | medium fit; `fasteners.md` |
| M3 / M4 / M5 / M6 nut (DIN 934) | across flats 5.5 / 7 / 8 / 10 mm | pocket needs +0.2–0.3 mm |
| M3 / M4 / M5 threaded rod | Ø 3 / 4 / 5 mm | through-hole for reinforcement: nominal + 0.3–0.5 mm |
| Heat-set insert M3 | Ø 4.0–4.6 mm boss hole | **always check the datasheet** — brands differ |
| 608 bearing | 22 × 8 × 7 mm | press seat = OD − 2 × interference, never 22 mm (`tolerances.md` §2) |
| 625 bearing | 16 × 5 × 5 mm | |

## Mounting surfaces (they decide the fixing the user must buy)

| Surface | Fixing | Safe pull-out per fixing |
|---|---|---|
| Plasterboard 12.5 mm, no stud | metal/spring toggle | 15–25 kg (plain plug ~5 kg = unsafe) |
| Plasterboard into stud | 4.5–5 × 60 mm wood screw | 40–60 kg; studs at 400/600 mm |
| Brick / concrete | Ø6 nylon plug + 5 × 50 mm screw | 40–80 kg; hollow brick ≈ plasterboard |
| Aerated concrete (Ytong) | spiral anchor | 10–20 kg; plain plugs pull out |
| Chipboard 16–18 mm | Ø4 × 30 mm screw or M4 through-bolt | 15–25 kg |

**An offset load is a moment, not a pull:** tension on the outermost fixing ≈
`F × standoff / fixing spacing` + its share of the direct pull. Compute **that** and
compare it against the table (`mechanics.md` §1) — a 5 kg towel at 40 mm standoff on
screws 60 mm apart adds ~3.3 kg to the top screw. Two fixings **in a vertical line**
resist rotation; two side by side do not.

Tile: drill through into the wall behind, never anchor in the tile. **Report surface,
fixing, count and the margin against the design load** (`strength.md`); ≥ 2 fixings on
anything that could rotate (L10).

## Electronics

| Object | Outline | Notes |
|---|---|---|
| Raspberry Pi 5 / 4 | 85 × 56 mm | mounting holes 58 × 49 mm, Ø 2.7 mm, 3.5 mm from edges |
| Arduino Uno | 68.6 × 53.4 mm | irregular hole pattern — look it up, do not assume symmetry |
| 18650 cell | 65 × Ø 18.4 mm | protected cells run to 70 mm — design for 70 |
| USB-C socket cut-out | 9 × 3.2 mm | plus clearance for the plug's shell |

## Cables and pipes

| Object | Diameter | Notes |
|---|---|---|
| Mains cable (H05VV-F 3G1.5) | Ø 8–10 mm | |
| Ethernet Cat6 | Ø 5.5–6.5 mm | |
| USB-C cable | Ø 3–4.5 mm | |
| Garden hose 1/2″ | Ø 18–20 mm outer | |

## When the number matters more than convenience

Anything load-bearing, press-fitted or safety-related does not come from this table:
look up the standard or datasheet, cite it, and put a tolerance on it. This table stops
confident guesses about everyday objects; it does not replace a drawing.
