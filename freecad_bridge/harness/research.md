# Playbook: research real-world dimensions, then model

Goal: when the request references a real object/standard whose dimensions you don't
know, gather trustworthy measurements first, then model parametrically.

## 1. Identify what must be looked up

- List the exact quantities you need (e.g. DIN 912 M6 socket-head cap screw head
  diameter and height, a standard EU pallet footprint, a Raspberry Pi 5 board outline
  and mounting-hole pattern).
- Only research what you cannot derive; don't look up things the user already specified.

## 2. Gather from the internet, then sanity-check

- Use your available web search/fetch tools to find the values. Prefer primary or
  authoritative sources: official standards (ISO/DIN/EN), manufacturer datasheets,
  mechanical drawings — over forum posts.
- Cross-check each critical number against a second source. Note units and tolerances.
- Record what you found and where: keep a short table of `quantity = value [unit]
  (source)` and include it in your final report so the numbers are auditable.

## 3. Turn findings into parameters

- Put every researched value into the `Parameters` Spreadsheet as an aliased cell, so
  the model documents its own dimensional assumptions and stays editable.
- If a value is uncertain, pick the standard/nominal value, alias it, and flag the
  assumption in your report.

## 4. Model and verify

- Build natively and parametrically as in the modeling playbook.
- Verify the finished geometry reproduces the researched dimensions via `model_digest`.

Deliver a real, validated, saved mutation and cite your dimensional sources.
