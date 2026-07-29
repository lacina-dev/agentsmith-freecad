# Playbook: quality assurance before declaring a model done

Goal: a repeatable checklist so a model is provably valid, manufacturable and
functional before you report success. Use the bridge measuring commands so each
check is numeric, not a guess.

## Geometry integrity

- `validate` passes with **no errors** and no unexpected warnings.
- Each body is a **single watertight solid**: use `check_solid` (one solid, closed,
  valid, no self-intersections). Stray shells/compounds or zero-volume objects are
  defects.
- No null shapes on visible objects; sketches fully constrained.

## Acceptance criteria from the feature plan

- Re-check the model against the **acceptance criteria written in the feature plan**
  (see the `model` playbook, step 3) — each is a number you committed to. Confirm every
  one from geometry (pass/fail with the measured value), not from intent.

## Requirements close-out

- Walk R1…Rn from the `core` requirements list **in order** and mark each **PASS** or
  **DEVIATION** with the evidence that closes it: **DIM** → the measured value and the
  command that produced it; **PARAM** → the alias and value from `model_digest` plus the
  parametric probe; **BUILD** → `print_readiness` / `slice_check.py` output; **SHAPE** →
  the `design.md` §7 review line covering it, with its measured value (a render alone
  closes nothing); **REPORT** → the paragraph in the final message.
- An R with no evidence line is **not done**. Check the REPORT rows **last and
  explicitly** — no geometric check can catch them, so they are the only class that fails
  silently. A requirement met by redefining it is a DEVIATION, not a PASS.
- Update `.agentsmith/requirements/<task_id>.md` with the final status/evidence columns.

## Functional pose — will it actually work in use?

- Re-state the functional pose from the `model` playbook (step 2): mounting surface,
  fastener axes, load vector, working-feature direction. Now **check the finished
  geometry against it**, feature by feature:
  - fastener holes are **perpendicular to the mounting plane** (a hole axis parallel to
    the wall cannot be screwed into it);
  - the working feature (hook opening, slot, spout, cavity) **projects/faces the right
    way when mounted** — not into the wall, not against gravity;
  - the load path runs from the working feature into the mounting surface.
- The cheapest test: render the model **in its mounted orientation** and ask "if I
  screw this on right now, does it do its job?" A part can pass every dimensional check
  and still fail this one — dimensional pass does NOT imply functional pass. A failure
  here is a defect to fix, never a footnote.
- **Prove it with numbers, not just the render** — `feature_probe` answers exactly this
  question and is what the grader uses:
  - `largest_plane.normal` is your mounting/contact surface; `largest_plane.standoff_mm`
    is how far the body actually stands off it. If that number is roughly your wall
    thickness, the working feature does not protrude and you have built a flat lookalike
    in the wrong projection plane (lesson L2) — no matter how right the silhouette looks.
  - every fastener hole's `axis` must be parallel to that normal (perpendicular to the
    mounting plane), or the screws cannot go in (lesson L1). Check `kind` too: a bore
    reported as `boss` means you added material where you meant to remove it.

## Dimensional correctness

- Re-pull `model_digest` and confirm **every requested dimension/tolerance** from
  geometry. Use `measure` for specific distances/gaps that the digest doesn't expose.
- Wall thickness, hole sizes and clearances meet the process minimums (see the
  relevant manufacturing playbook).
- **Internal geometry** (wall thickness, pockets, bores, ribs) can't be trusted from an
  external render. Use the bridge `cross_section` command for **numeric section
  evidence** — it returns section area, closed-wire count and section bbox per solid.
  A slice through the wall/pocket proves the true thickness and that the cavity exists;
  cite the section numbers, not just an outside screenshot.

## Assembly / fit (if more than one part)

- Run `interference_check` on parts that must fit: mating parts should have the
  intended clearance, and parts that must not collide must show **no interference**.

## Physical sanity

- `mass_properties` → check **mass and centre of mass** are plausible for the material
  and use (e.g. not accidentally hollow/solid, CoM where you expect for stability).
- For load-bearing parts, confirm the strength sizing (see the `strength` playbook):
  working stress ≤ allowable, margin of safety ≥ 0.

## Pre-mortem — how does this fail in service?

Take each functional feature (hook, boss, snap, lid, mount, hinge) and name its most
likely failure mode, the geometry that prevents it, and the number that proves it:
`mechanism → countermeasure (number) → residual risk`. At minimum sweep six:

- **Overload / misuse** — someone leans on it, yanks it, over-tightens the screw. Check
  the weakest section against that load, not the nominal one (`strength` playbook); for a
  screw into plastic, installation torque is the worst case, not service load.
- **Weakest section** — thinnest wall, the layer-adhesion plane, the boss around a screw.
  Prove the thickness with `cross_section`, never from a render, and check that the load
  does not pull across printed layers (`print3d` orientation).
- **Time** — creep under sustained load, fatigue at a flex, UV / heat / moisture, wear at
  a sliding face (see the `materials` playbook).
- **Print variation** — recompute every **clearance** fit with the mating dimension at
  −0.3 mm and +0.3 mm (`tolerances` band); one that works only at nominal does not work
  (widen it, or add a lead-in chamfer / relief slit / crush rib). An **interference** fit
  cannot survive ±0.3 mm and must never be widened: state the retention mechanism that
  absorbs the variation (crush ribs, split/slotted boss, set-screw, retainer), the
  criterion it must meet (the outer race does not spin), and flag the seat as a
  test-print-and-tune parameter.
- **Mount** — the fixing, not the part: anchor against the **stated** wall, tension per
  fixing **including the moment** (`reference-dimensions` table, `mechanics` §1), what
  stops rotation. One screw is a hinge (L10); a plain plug in plasterboard is ~5 kg and
  unsafe. State surface, fixing, count and margin.
- **Assembly and service** — can a real tool reach every fastener, and does the part come
  apart again? Prove access with `interference_check` against the placed fastener or a
  dummy cylinder on the driver axis. **Scratch geometry is created, read and DELETED in
  the same step**, before `validate`/`save` — a probe body left behind corrupts every
  bbox check, `mass_properties` and the final render; if you cannot delete it, do the
  check by arithmetic on `feature_probe` axes.

"It should be fine" is not an answer. Each mode is either fixed now or reported as a
stated limit with the load or condition at which it applies.

## Visual comparison against the reference / target

- Render a clean **axonometric** view of the finished model (`set_view` iso →
  `fit_view` → `screenshot`) and put it **beside** the reference images (from the
  `reference` playbook, `.agentsmith/reference/<task_id>/`) or the before-image for a
  fix.
- **Enumerate matching vs deviating features** — topology, proportions, functional
  interfaces. For each deviation, either **fix it** or **report it as a conscious,
  stated trade-off** (e.g. a cosmetic detail deliberately left out of scope). Do not
  leave a silent shape mismatch.
- **Check the projection, not just the silhouette.** Identify which view each reference
  image shows (see the `reference` playbook §4b) and render the model **from that same
  viewpoint** for the comparison — then also render the **orthogonal views**. A model
  that shows the reference silhouette in one view but is a featureless thin slab in the
  view where the working feature must protrude (e.g. the side view of a wall hook) has
  the profile in the **wrong projection plane** — matching one view proves nothing.
  This is a functional defect even when the primary comparison "looks right".
- Remember: the render is **shape-only evidence**. Never read dimensions off it —
  dimensions come from `model_digest` / `measure` / `cross_section`.

## Manufacturing readiness

- Confirm the design obeys the rules of its intended process (print / molding /
  sheet metal / machining): overhangs, draft, bend radii, tool access, etc.
- **For printed parts, `slice_check.py` output is the strongest printability evidence** —
  the real slicer, not a geometric estimate. Run the `print3d.md` §6 gate, cite its numbers
  (print time, filament grams, layer count) and confirm no supports on a support-free
  design.

## Report

- State each check and its measured result (pass/fail with numbers), the objects and
  parameters changed, and any residual risk; then the two `core` blocks. Only then claim
  success.
