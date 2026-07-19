# Playbook: pattern library of reusable mechanical micro-recipes

Goal: recurring mechanical features (a screw boss, a snap, a rib) have well-known,
verified build recipes. Don't reinvent them per task — read the specific pattern and
follow it, so every model uses the same conservative, parametric approach.

## How to use this library

- Each pattern is a short detail file at
  `freecad_bridge/harness/patterns/<name>.md`. They are **not** injected automatically
  — read the one you need on demand:
  `cat freecad_bridge/harness/patterns/<name>.md`
- Read **only the pattern(s) the part actually uses**; there is a time budget.
- Each pattern gives: **When to use**, **Key parametric relationships/ratios** (with
  formulas), a **FreeCAD build recipe** (sketch→feature bound to `Parameters` aliases),
  **Pitfalls**, and **Verify** (which bridge checks confirm it).
- These are **guidance, not gospel** — conservative, widely-cited rules of thumb. Where
  a value is process- or material-dependent, the pattern states the assumption; adjust
  it in the spreadsheet and record the change in your report.

## Available patterns

| Pattern | `cat` this file | Use when |
|---------|-----------------|----------|
| Threaded boss | `patterns/threaded_boss.md` | a screw (self-tapping or machine) drives into a molded/printed boss |
| Cantilever snap-fit | `patterns/snap_fit_cantilever.md` | two parts clip together tool-lessly with a flexing hook |
| Rib / gusset | `patterns/rib_gusset.md` | a wall, boss or bracket needs stiffening without thickening |
| Heat-set insert | `patterns/heat_set_insert.md` | a brass threaded insert is melted into a plastic boss |
| Hinge clearance | `patterns/hinge_clearance.md` | a print-in-place or pinned knuckle hinge must rotate freely |
| Screw counterbore | `patterns/screw_counterbore.md` | a cap- or flat-head screw must sit flush/recessed in a clearance hole |
| Living hinge | `patterns/living_hinge.md` | a one-piece thin flexure folds repeatedly (lid/clamshell) — printed flat in PP/PETG/PA, not PLA |
| Press-fit bearing | `patterns/press_fit_bearing.md` | a pocket must hold a standard ball bearing (e.g. 608) on its outer race |
| Dovetail slide | `patterns/dovetail_slide.md` | two parts slide/lock via a trapezoidal dovetail rail and groove |

All dimensions stay in the `Parameters` spreadsheet as aliases so the feature remains
editable; the patterns show which relationships to encode as expressions.
