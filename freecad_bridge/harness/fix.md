# Playbook: fixing / refining an existing model

Goal: correct a specific defect the user described, at its root, without regressions.

## 1. Reproduce the complaint numerically

- Read the user's complaint and translate it into a measurable condition (e.g. "the
  inset in the chamfered corners must be 5 mm" → measure the actual perpendicular
  offset in those corners).
- Get `model_digest`, then measure the *specific* feature the user is unhappy about.
  Confirm the defect is real and quantify the current vs. desired value before editing.

## 2. Find the driver, not the symptom

- Trace which parameter/expression/datum controls the wrong dimension. The offending
  value almost always comes from a Spreadsheet cell or a sketch expression.
- Beware of hidden couplings: a single parameter (e.g. a "corner size") may map to a
  different effective leg/offset through geometry. Compute the real relationship from
  `model_digest` rather than assuming `parameter == result`.

## 3. Apply the smallest correct change — decisively

- As soon as you have identified the driver and the target value, change it through the
  bridge. Do not keep investigating for perfect certainty; a reversible parametric edit
  you then verify is faster and safer than endless analysis.
- Fix the general driver so the correction holds if the model's dimensions change later.

## 4. Verify and guard against regressions

- Re-measure the corrected feature: it must hit the requested value/tolerance.
- Re-check neighbouring dimensions the edit could have disturbed (widths on adjacent
  edges, symmetry, other corners) so you don't trade one defect for another.
- `validate`, `save`, `fit_view`, final `screenshot`, and report before→after numbers.
