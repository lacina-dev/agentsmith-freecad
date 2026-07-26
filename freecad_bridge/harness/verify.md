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
- **For printed parts, `slice_check.py` output is the strongest printability
  evidence** — it is the real slicer, not a geometric estimate. Run it
  (`print3d.md` § "Slice check"), cite its numbers (print time, filament grams,
  layer count) and confirm no supports were generated on a support-free design.

## Report

- State each check and its measured result (pass/fail with numbers), the objects and
  parameters changed, and any residual assumption or risk. Only then claim success.
