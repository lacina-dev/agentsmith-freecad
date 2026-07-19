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
