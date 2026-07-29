# Lessons from real failures (always in effect)

Distilled, binding rules born from tasks that actually failed or were rejected by the
user. Each entry is one incident → one rule. When a rule matures into a full playbook
section, the entry shrinks to a pointer. Maintainers append entries after every
failed/rejected task; the agent must treat every rule here as non-negotiable.

## L1 — Functional pose before geometry (2026-07-18, wall hook)
A hook was modeled as its side profile extruded flat: every dimension passed, the part
was unusable on the wall. **Rule:** state mounting surface, fastener axes, load vector
and working-feature direction BEFORE modeling, and verify the finished part against
them. → promoted: `model` step 2, `verify` "Functional pose", core DoD item 6.

## L2 — Map a reference image to its projection (2026-07-18, coat hook vesak3)
A reference photo (side profile) was faithfully rebuilt as the FRONT view — silhouette
matched, part was a flat lookalike. Matching one view proves nothing. **Rule:** for
every reference image decide which view it shows, write the three-view description in
mounted pose, and check the working feature actually protrudes along the
away-from-mounting axis. → promoted: `reference` §4b, `verify` projection check.

## L3 — Bridge units are explicit (2026-07-18, wall hook v2)
`mass_properties` was fed density `0.00127` (g/mm³) instead of **kg/m³** → mass came
out ~10⁶× too small and nobody noticed until review. **Rule:** check the unit every
bridge argument expects; sanity-check every computed mass/volume against common sense
(a hand-sized PETG part weighs grams to tens of grams). → promoted: `materials` notes.

## L4 — A finished model must be visible and framed (2026-07-18)
A correct model was delivered invisible; the user saw an empty viewport and read it as
failure. **Rule:** end every task with result bodies Visible (construction inputs
hidden), `fit_view`, screenshot. → promoted: core DoD item 7.

## L5 — Don't forage for infrastructure (2026-07-18)
A reviewer burned budget hunting for `freecad_bridge_client.py` in sibling folders.
**Rule:** the client is copied into the task's working directory by the panel — use
`python3 freecad_bridge_client.py …` relative to the project directory and do not go
looking elsewhere; if it is genuinely missing, say so instead of searching the disk.

## L6 — Verify the machine, not the config, before a print (2026-07-24, cable clip)
A test clip was sliced from `slicer-config.json` and uploaded without ever asking the
printer what nozzle and filament it actually had. The config happened to be right, so
nothing broke — but the check that would have caught a wrong spool was simply missing.
A config records intent; only the machine knows reality, and a 40-minute print is not
a cheap place to discover the difference. **Rule:** before starting any print, compare
the nozzle diameter and filament type from the G-code header against the printer's own
report AND against the material the user named in the prompt; treat "could not verify"
as unresolved rather than as a match. → promoted: `manufacture` print step 4,
`print_send.py --send` pre-flight (blocks `--start` on mismatch).

## L7 — A remote print start assumes an empty bed (2026-07-25, towel hook)
A print was started over the network two hours after the previous one, with nobody
confirming the bed was clear. The printer dropped into `ATTENTION` right after the start.
Pre-flight can compare nozzle and filament, but **no API reports the state of the bed** —
and a finished part from the last print sitting under the probe stops a start just as
reliably as the wrong nozzle. **Rule:** before a remote `--start`, get it confirmed that
the bed is empty and the previous print removed; uploading G-code is reversible and may
happen without asking, spinning up a print is not. → promoted: `manufacture` step 4.

## L8 — CANCELLED: the fault was in the eval, not in the model (2026-07-26)
This entry used to claim that the models had produced a press fit at a nominal 22.0 mm
three times over. **That was not true.** The check in `bearing_block_608.json` read the
alias `BearingOD` and demanded it be under 21.95 — but `BearingOD` is by definition
**22 mm, that is the bearing diameter**. The model had `PressInterference = 0.075` and
`BoreDia = 21.85` all along, which is exactly right; confirmed on the geometry too
(cylindrical face Ø21.85). With the alias fixed to `BoreDia` the same model scores **9/9**.

The lesson survives, but it points elsewhere. **Rule:** before you turn a repeated failure
into a rule for the agent, **verify on the geometry that the check measures what you think
it does** — for an alias check, read what that alias means per the brief. Three
"occurrences" were three runs of one broken check, and the lesson written from them taught
the agent to fix something it was doing correctly. Same class of error as dates once
computed as geometry, or flipped normals — the eval measured something other than reality
and the model took the blame. → promoted: `bearing_block_608.json` (alias fixed to `BoreDia`).

## L9 — Everyday dimensions are looked up, not estimated (2026-07-25, paper-towel holder)
A kitchen paper-towel holder was given 250 mm and the roll does not fit. Measured: a Czech
roll ~230 mm, a German one ~260 mm, an American 11″ = 279 mm, diameter 105–150 mm. The
research playbook **was** in the harness, it just never fired — its trigger read "when the
spec references an object whose dimensions must be looked up", and with a kitchen roll a
person (and a model) assumes they already know it. It is exactly the everyday objects where
a confident estimate fails, because nothing forces a check. **Rule:** anything the part
holds, carries or mates with gets its dimensions **written down with a source before the
first geometry exists** — even when you "know" the object. Size for the **largest common
variant plus clearance** and state which variant the part is designed for; a part that fits
only the smallest version is broken for most people. → promoted: `core` (section "Before any
geometry: what does it hold?", DoD item 8), new playbook `reference-dimensions`.

## L10 — "They touch, so they hold" is not a joint (2026-07-25, feedback from practice)
Multi-part assemblies were built without ever stating what holds them together — face on
face and hope. Two concrete traps: a part on **one screw is a hinge** (it holds, but it
rotates), and a **printed thread or clip** where force acts survives a fraction of what
bought hardware does. **Rule:** for every interface write down **what holds it** and **what
prevents loosening and rotation**, before any geometry exists. At load-bearing points reach
for **standard fasteners** (screw into a counterbore, nut in a pocket, threaded rod through
the part), and for long printed parts consider a **threaded rod as a stiffener** — plastic
is weak between layers, a steel rod makes it steel's problem. State in the report what the
user has to buy. → promoted: `core` DoD item 9, `assembly` §3 and §3b, `fasteners` §6.
