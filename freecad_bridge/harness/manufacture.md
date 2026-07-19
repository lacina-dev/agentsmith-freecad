# Playbook: manufacturing pipeline (slice → print) driven from chat

Goal: the user drives the whole pipeline conversationally — "naslicuj to",
"vytiskni to", "pošli to na tiskárnu", or a combined "udělej model a rovnou ho
vytiskni". These are legitimate tasks even when they change no geometry.

## Tools (copied into the project directory by the panel)

- `python3 slice_check.py --from-bridge --printer <key> --outputdir .agentsmith/gcode`
  — export the live model, slice it with OrcaSlicer for the chosen printer,
  persist the G-code, and report real stats (time, filament, supports, warnings).
  `--list-printers` shows the fleet.
- `python3 print_send.py --status --printer <key>` — is the printer reachable?
- `python3 print_send.py --send <file.gcode> --printer <key> [--start]` — upload
  (and optionally start) the print.

## Printer selection — ask by offering, never guess silently

- If the user names a printer (Prusa / CORE One, QIDI / Max3, WOLF), map it to
  the config key (`slice_check.py --list-printers`) and use it.
- If the user does NOT name one, slice for the **default printer** and say so in
  the report, **always listing the other options**: e.g. "Naslicováno pro Prusa
  CORE One (výchozí). Řekni ‚naslicuj pro QIDI' pro přeslicování, nebo ‚další
  slice pro wolfa' až budou jeho profily."
- An **unconfigured** printer (missing profiles) is not an error to hide: state
  plainly that its profiles are pending and offer the configured ones.
- Structural parts: mention that CORE One has a STRUCTURAL process variant (see
  slicer-config.json comment) when the part is load-bearing.

## Slice tasks ("naslicuj to")

1. The model must already pass verification (`validate`, `check_solid`) — do not
   slice a broken solid; fix it first or report why not.
2. Run slice_check with `--outputdir .agentsmith/gcode` so the G-code persists
   for a later "vytiskni to". Name and path go in the report.
3. Read the stats critically: supports where the design was meant to be
   support-free, warnings (outside bed, empty layers), or absurd time/filament
   are DEFECTS to investigate — not lines to paste and ignore.
4. Report: printer used, print time, filament g, supports yes/no, warnings,
   G-code path, and the other-printer offer.

## Print tasks ("vytiskni to")

1. Find the G-code: the newest matching file in `.agentsmith/gcode/` (its name
   encodes the printer key). If it was sliced for a DIFFERENT printer than the
   user now wants, re-slice first — G-code is printer-specific, never cross-send.
2. `print_send.py --status` first. Not reachable (typically away from the
   printers' home network): report that the G-code is ready and exactly what
   command will send it later — that is a SUCCESSFUL outcome of this task, not a
   failure. Do not retry endlessly.
3. Reachable: upload with `--send`; use `--start` only when the user asked to
   actually print (they did if they said "vytiskni"). Report the printer's
   response.

## Combined tasks ("udělej model a rovnou ho vytiskni")

Run the FULL modeling pipeline first (model → verify → reviewer-grade quality),
then slice, then attempt the print step — all in this one task. The modeling
part follows every normal playbook; the manufacturing part follows this one.
Budget accordingly: leave the last quarter of your time for slice + print.

## Supervisor contract for action-only tasks

The supervisor normally requires a live document mutation for success. When the
task legitimately changes nothing (slice-only, print-only, status query), end
your final message with the EXACT line:

`ACTION-ONLY TASK COMPLETED: <one-line summary of what was done>`

Only use it truthfully — emitting it after a failed slice/upload is a false
report. A combined model+print task mutates the document, so the normal success
path applies and this marker must NOT be used.
