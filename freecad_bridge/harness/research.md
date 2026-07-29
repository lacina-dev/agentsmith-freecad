# Playbook: acquiring the numbers you don't have

Goal: every dimension traces to a source. Everyday-object ranges are tabulated in
`reference-dimensions.md`; this playbook is *how* to get a number you don't have.

## 1. Where this starts

`core`'s six-point gap sweep and its LOOKUP / DERIVABLE / ASSUME sort run first; the
**LOOKUP** bin lands here. This playbook is only *how* to get the number. Do not look up
what the user already gave you.

## 2. Sources: prefer primary, never invent one

- Standards (ISO/DIN/EN), datasheets and dimensioned drawings beat vendor pages, which
  beat forum posts. Note units and tolerances.
- **Cross-check every critical number against a second, independent source.** Where they
  disagree, keep the range rather than averaging it away.
- **Never fabricate a citation.** With no web tool, say so and tag the value `RECALL` —
  an invented URL or standard number is worse than an honest assumption, because it
  cannot be checked.

## 3. Record it, auditably

- Report a table of `quantity = value [unit] (core provenance tag)`.
- Put every researched value into `Parameters` as an aliased cell, so the model documents
  its own assumptions and stays editable.
- Verify the geometry reproduces those numbers (`model_digest`) and state which
  variant/standard you designed for.
