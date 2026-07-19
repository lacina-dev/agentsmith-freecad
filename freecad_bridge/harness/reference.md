# Playbook: ground shape and function in real-world references

Goal: when the request names a **real-world object, product or mechanism**, ground
its *shape and function* in references before modeling. This is distinct from
`research` — that playbook grounds **numbers/standards** (concrete mm), this one
grounds **topology, proportions, functional interfaces and how the thing moves**.
The two are complementary: references tell you *what it looks like and how it
works*; the Parameters spreadsheet (fed by `research`) tells you *how big*.

## 1. Decide whether a reference is needed

- **No reference:** generic shapes with no real-world identity — a plate, a box, a
  bracket, a cylinder, "a hexagonal standoff". You already know their form.
- **Yes, reference:** a **named** real product/part/mechanism whose form is
  non-obvious or whose function constrains the geometry — e.g. a NEMA 17 stepper, a
  GoPro mount, a bench vise, a carabiner, a bicycle chain link, a specific connector
  housing, a "Prusa-style" spool holder. If you cannot draw it correctly from memory,
  you need references.
- When in doubt, spend one search deciding — a single mis-imagined mechanism wastes
  more of the time budget than a quick grounding pass.

## 2. Gather 3–6 sources from DIFFERENT angles

- Aim for **3–6 sources**, each showing a *different* facet: overall form, a
  cross-section/exploded view, the mating interface, the moving mechanism.
- **Prefer CAD-grade sources** over random photos, in this order:
  - CAD portals: **GrabCAD**, **McMaster-Carr** (has CAD + 2D drawings for real parts),
    **Printables / MakerWorld / Thingiverse** (real printed designs of the object).
  - Manufacturer **datasheets and dimensioned drawings** (also feed `research`).
  - Only then general photos — and treat them as shape-only, like model screenshots.
- **First check the local reference dir** — the panel may have pre-attached user
  reference images (or passed them as vision input):
  `ls .agentsmith/reference/<task_id>/`
  If images are already there, use them; they are the user's intended target.
- **Download** each new reference image you find into that same dir so the run is
  self-contained and auditable:
  `.agentsmith/reference/<task_id>/` (create it if missing; use the task_id
  from the panel — if none was supplied, use a short slug of the object name).

## 3. Write the reference manifest

- Record what you gathered in
  `.agentsmith/reference/<task_id>/reference-manifest.json` as a JSON **array** of
  objects, one per source:

  ```json
  [
    {
      "source_url": "https://...",
      "why_trustworthy": "McMaster CAD drawing of the actual part number",
      "what_it_shows": "cross-section revealing the internal cam and spring seat",
      "assumptions": "assume symmetric halves; lever pivot centered on body"
    }
  ]
  ```

- Keep it honest and specific — this file is **cited in your final report** so the
  grounding is auditable. If a source is weak (a blurry photo, a look-alike), say so
  in `why_trustworthy` and lean on a better source for that feature.

## 4. Separate SHAPE from DIMENSION

- References give you **proportions, topology and functional interfaces** — how many
  knuckles a hinge has, that a lever pivots at one end, that a rail is a dovetail, the
  ratio of head to shaft. They do **not** give you millimetres.
- **Never scale dimensions off a reference image.** Pixels are not mm; perspective and
  crop lie. Concrete dimensions come from `research` → the `Parameters` spreadsheet.
- Convert what you see into *relationships and counts* you can drive parametrically
  (e.g. "5-knuckle hinge", "jaw travel ≈ body length", "3 fins equally spaced").

## 4b. Map each reference image to a PROJECTION before modeling

A photo is one projection of a 3D object — reproducing its silhouette in the wrong
projection plane yields a flat lookalike that fails in use (observed live: a wall-hook
photo shows the SIDE profile; a worker rebuilt that silhouette as the FRONT view, so
the prong bent sideways along the wall and nothing protruded from it).

- For every reference image, first decide **which view it shows**: front, side, top, or
  perspective — and write it down. Judge from function, not from framing: for bent-strip
  hardware (hooks, hangers, brackets, clips) the characteristic bent profile is almost
  always the **side view**, i.e. the plane **perpendicular to the mounting wall** that
  contains the load vector and the out-of-the-wall direction.
- Then write the object's **three-view description in mounted pose** (front / side /
  top, one line each) before touching geometry — e.g. wall hook: *side = bent J/7
  profile projecting from the wall; front = narrow vertical strip, holes on its
  centerline; top = thin strip cross-section with the prong sticking out of the wall.*
- Cross-check against the functional pose (`model` playbook step 2): the profile plane
  must contain the load vector and the away-from-wall direction; hole axes stay
  perpendicular to the mounting plane. If your planned sketch plane puts the reference
  silhouette into a view where the working feature cannot protrude from the mounting
  surface, the plane is wrong — rotate the plan, not the finished solid.

## 5. Capture the MECHANISM (functional objects)

- For anything that moves or mates, write down **what slides/rotates/mates into what**:
  the pivot axis, the sliding direction, which faces contact, which feature retains
  which. This is the functional skeleton of the model.
- Feed this into the assembly and fastener playbooks: the mechanism dictates part
  splits, clearances (`tolerances`), pivots/pins (`fasteners`, `hinge_clearance`
  pattern) and mate directions (`assembly`). Model the interface, then the cosmetics.

## 6. Use the reference as a VISUAL ACCEPTANCE TARGET

- The references define what "looks right" means for this task — matching them **is
  part of the task**, not optional inspiration. Your **feature plan must name the
  reference features you are reproducing** (counts, arrangement, curves, interfaces),
  so the mapping reference → geometry is explicit before you build.
- When the model is built, render a clean **axonometric** view (`set_view` isometric
  → `fit_view` → `screenshot`) and put it **beside** the references. Enumerate:
  - **Matches:** features that reproduce the reference (topology, proportions, mates).
  - **Deviations:** anything different, and whether it is a bug to fix or a conscious,
    stated trade-off (e.g. simplified cosmetic detail out of scope).
- Fix real shape defects; report the rest. This is the same comparison the `verify`
  playbook requires — reference images are the target it compares against.

Deliver the manifest, cite your sources in the report, and confirm the final render
matches the referenced shape and function.
