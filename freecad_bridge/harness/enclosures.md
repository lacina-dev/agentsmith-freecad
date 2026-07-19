# Playbook: enclosures, housings and boxes

Goal: functional housings that assemble, mount and route cleanly — a common real task.

## 1. Architecture

- Split into logical parts (base + lid, or two half-shells). Decide the **parting line**
  and how they join (screw bosses, snap-fits, lip/tongue).
- Keep a **uniform wall thickness** (typ. 1.5–3 mm for printed/moulded plastic);
  reinforce with ribs rather than thick walls.

## 2. Joining the lid

- Use a **lip/step joint** (tongue-and-groove) so the lid locates and light/dust is
  blocked; leave ~0.2 mm clearance on the lip for fit.
- Fasten with **screw bosses** (see `fasteners`) at corners and along long spans, or
  snap-fits for tool-less assembly.

## 3. Internal features

- Add **bosses** for PCB/component mounting; standoffs sized for the screw/insert.
- Add **ribs** (thickness ~50–60% of wall) for stiffness; fillet rib roots.
- Provide **cable routing / strain relief**, and cut-outs for connectors, buttons,
  LEDs, ventilation with proper clearance around each.

## 4. Ergonomics & robustness

- Fillet outer edges for feel and strength; avoid sharp internal corners (stress
  risers) — add fillets.
- Add **mounting features** (flanges, keyholes, screw tabs) if it mounts to something.
- Consider **draft** if it will be moulded (see `molding`); consider print orientation
  and overhangs if printed (see `print3d`).

## 5. Verify & deliver

- `interference_check` base vs lip vs lid: parts locate with the intended clearance and
  don't collide; screw bosses align between halves (`measure` the bolt pattern).
- Confirm wall thickness and internal clearances meet the process minimums.
- Report the joint scheme, fastening, wall thickness and mounting provisions.
