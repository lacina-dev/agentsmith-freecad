# Playbook: dimensions of everyday objects

For parts that hold or fit something that already exists. The numbers below are
**ranges, not constants** — that is the whole point of the table. Most of these
objects vary by market and brand by tens of millimetres, which is exactly enough
to turn a holder into a decoration.

## How to use this table

1. Find the object. If it is not here, look it up (see the "Průzkum" playbook) —
   do not guess because it feels familiar.
2. **Design for the top of the range**, then add clearance. A holder built for the
   smallest variant is broken for everyone else; one built for the largest merely
   looks slightly generous with a small roll.
3. Write the chosen value and the variant into the `Parameters` spreadsheet, and
   say in your report which variant you designed for.
4. Cross-check anything critical against a second source. Where the sources here
   disagreed, the disagreement is left in rather than averaged away.

Clearance starting points (FDM, tune to the print): sliding/loose fit 0.4–0.8 mm
per side; something the user drops in and pulls out 1–3 mm per side; anything
that must work when the print came out slightly oversized, more.

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
| M3 / M4 / M5 / M6 screw | clearance hole Ø 3.4 / 4.5 / 5.5 / 6.6 mm | medium fit; see "Spojovací materiál" |
| M3 / M4 / M5 / M6 nut (DIN 934) | across flats 5.5 / 7 / 8 / 10 mm | pocket needs +0.2–0.3 mm |
| M3 / M4 / M5 threaded rod | Ø 3 / 4 / 5 mm | through-hole for reinforcement: nominal + 0.3–0.5 mm |
| Heat-set insert M3 | Ø 4.0–4.6 mm boss hole | **always check the datasheet** — brands differ |
| 608 bearing | 22 × 8 × 7 mm | press seat is NOT 22 mm; see lesson L8 |
| 625 bearing | 16 × 5 × 5 mm | |

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

Anything load-bearing, press-fitted or safety-related does not get taken from this
table. Look up the actual standard or datasheet, note the source in your report,
and put a tolerance on it. This table is here to stop confident guesses about
everyday objects — not to replace an engineering drawing.
