# AgentSmith — AI modeling agent for FreeCAD

AgentSmith turns FreeCAD into an AI-driven parametric modeling workstation: a
chat panel inside FreeCAD hands tasks to an AI backend (Codex / Claude Code /
Copilot), which edits the **live document** through a supervised local bridge —
grounded by an engineering-knowledge harness, verified by an independent
reviewer pass, and connected to a real slicer for print-readiness feedback.

## What it does

- **Model from a prompt or a reference image** — reference photos are attached
  as vision input; the harness forces interpretation-first modeling (functional
  pose, projection mapping, feature plan) before any geometry.
- **Engineering knowledge built in** — playbooks for materials (FDM + anisotropy),
  mechanics sizing, 3D-print design rules, tolerances, fasteners, enclosures,
  plus a pattern library (snap-fits, threaded bosses, hinges, …).
- **Verified, not vibed** — geometry checks (`model_digest`, `cross_section`,
  `print_readiness`), an independent read-only reviewer with an optional
  one-shot auto-fix round, and an objective eval harness with golden tasks.
- **Manufacturing from chat** — "naslicuj to" / "vytiskni to": OrcaSlicer
  integration (multi-printer fleet config) and printer upload via
  PrusaLink/Moonraker.
- **Safety** — every task runs against a protected document with checkpoints,
  a watchdog budget, mutation guards and automatic rollback on failure.

## Layout

| Path | What it is |
|---|---|
| `freecad_bridge/` | The FreeCAD addon: bridge server + chat panel (`BridgeGui.py`), agent tools (`slice_check.py`, `print_send.py`, `freecad_bridge_client.py`), `slicer-config.json` |
| `freecad_bridge/harness/` | The modeling harness: playbooks, pattern library, lessons from real failures (`registry.json` indexes them) |
| `eval/` | Objective scoring: `run_eval.py` grades the open model against golden tasks; `run_e2e.py` runs full agent sessions headlessly |
| `install.sh` | Symlinks the addon into the FreeCAD user `Mod` directory |
| `AGENTS.md` | Project instructions for AI agents working in this repo |

## Install

```bash
./install.sh          # symlink the addon into FreeCAD's Mod directory
# restart FreeCAD → select the AgentSmith workbench → Start bridge
```

Requirements: FreeCAD 1.x, an AI backend CLI on PATH (`codex`, `claude` or
`copilot`), and OrcaSlicer (AppImage; see `freecad_bridge/slicer-config.json`)
for slicing support.

## Evaluation

```bash
python3 eval/run_eval.py --all        # grade the currently open model
python3 eval/run_e2e.py --all         # full end-to-end agent runs (costs tokens)
```
