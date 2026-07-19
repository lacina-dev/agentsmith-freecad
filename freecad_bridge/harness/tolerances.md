# Playbook: tolerances, fits and functional dimensioning

Goal: dimension parts so they actually fit and function, matching the manufacturing
process's achievable precision. Values are typical; confirm against the process.

## 1. Dimension from function, not convenience

- Identify **functional dimensions** (mating faces, hole centres, interfaces) and
  constrain those directly to datums. Let non-critical dimensions float within a
  general tolerance.
- Use a consistent **datum/reference scheme** so tolerances don't stack up across many
  chained features (avoid tolerance stack-up on critical fits).

## 2. Fits (ISO 286 hole-basis, common choices)

- **Clearance / free-running (e.g. H7/g6):** parts move freely (shaft in bearing).
- **Location / slip (H7/h6):** precise location, easy assembly, negligible play.
- **Interference / press (H7/p6):** permanent press-fit; needs force to assemble.
- Apply the tolerance to whichever member you control; keep the other at the basic
  size. For a 20 mm nominal, H7 hole ≈ +0.000/+0.021 mm as a sense of scale.

## 3. General tolerances

- Where nothing tighter is needed, assume a general-tolerance class (ISO 2768-m for
  general machining). Don't over-tighten — tight tolerances cost money and print/mould
  time; reserve them for functional features.

## 4. Process-realistic precision

- **Machining:** can hold tight ISO fits directly.
- **FDM print:** holes print undersized and dimensions vary ±0.1–0.3 mm; design fits
  with **printed clearance** (~0.2 mm snug, 0.4–0.5 mm free) rather than ISO shaft/hole
  pairs, or plan to ream/drill critical holes to size.
- **Injection moulding:** account for shrinkage and draft; tolerances looser than
  machining.

## 5. Deliver

- Put nominal sizes and the chosen clearance/fit values in the `Parameters`
  Spreadsheet (e.g. `bore`, `shaft`, `fit_clearance`) and derive mating dimensions by
  expression so the fit stays consistent if sizes change.
- Verify the actual gap with `measure`/`interference_check`; report the fit and the
  achieved clearance.
