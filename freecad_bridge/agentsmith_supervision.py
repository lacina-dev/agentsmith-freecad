"""Pure decision logic for supervising a live-editing task.

Extracted from BridgeGui so it can be unit-tested without FreeCAD or Qt. This
module MUST NOT import FreeCAD, FreeCADGui, Part or PySide — it takes already
measured facts and returns decisions. It answers the three questions the panel
used to answer inline:

  * is the protected document still intact, or must it be restored?
    -> ``restore_reason``
  * did the running task trip a safety limit? -> ``evaluate_guard``
  * did the backend actually deliver, and what happens to the document now?
    -> ``classify_outcome``

Module name is prefixed because every FreeCAD Mod directory shares sys.path;
a generic ``supervision`` could collide with another addon.
"""

# The exact line a worker must print for a task that legitimately changes
# nothing (slice / print / query) to count as delivered.
ACTION_ONLY_MARKER = "ACTION-ONLY TASK COMPLETED"

# Follow-up action the panel must take on the document after classification.
COMMIT_AND_SAVE = "commit_and_save"
ABORT_TRANSACTION = "abort_transaction"
RESTORE_SNAPSHOT = "restore_snapshot"

# The protected FCStd must be missing this many consecutive guard ticks before
# the in-memory backup is written back. A single miss is normal: FreeCAD's save
# is not atomic, so the file briefly disappears while being rewritten.
MISSING_FILE_TOLERANCE = 3

# FreeCAD memory growth that ends a task (runaway geometry / leaking loop).
RSS_GROWTH_LIMIT_BYTES = 1024 * 1024 * 1024


def restore_reason(guard_violation=None, document_missing=False,
                   path_changed=False, archive_invalid=False):
    """Why the task's document must be rolled back to its snapshot, or None.

    Order matters: a guard violation recorded while the task ran outranks
    anything observed afterwards, because it names the earlier, root cause.
    """
    if guard_violation:
        return guard_violation
    if document_missing:
        return "active document was closed"
    if path_changed:
        return "document path changed"
    if archive_invalid:
        return "canonical FCStd is missing or corrupt"
    return None


# --------------------------------------------------------------------------- #
# Runaway-mutation guard
# --------------------------------------------------------------------------- #
#: Total live mutations after which a task is considered stuck. This is the real
#: runaway guard: a genuine loop reaches it in seconds, and no legitimate build
#: has come close.
TOTAL_MUTATION_CAP = 3000

#: Object types that are BUILT one event at a time. A sketch fires an event per
#: line and per constraint; a spreadsheet fires one per cell and per dependent
#: recompute. For these, a high event count is a measure of detail, not of being
#: stuck, so the per-object heuristic barely applies.
INCREMENTAL_TYPE_PREFIXES = ("Sketcher::", "Spreadsheet::")
INCREMENTAL_OBJECT_CAP = 1500

#: Everything else. Raised from the original 180 after that number killed two
#: correct builds -- a spreadsheet-driven wall hook, then a towel hook whose
#: profile sketch legitimately took over 180 edits. The per-object count was
#: never a good measure of "stuck": it measures how detailed an object is. It is
#: kept only as an early hint, well above anything a real part has needed, with
#: TOTAL_MUTATION_CAP doing the actual work.
DEFAULT_OBJECT_CAP = 600


def object_event_cap(type_id):
    """How many events one object may accumulate before it looks pathological."""
    if any((type_id or "").startswith(prefix) for prefix in INCREMENTAL_TYPE_PREFIXES):
        return INCREMENTAL_OBJECT_CAP
    return DEFAULT_OBJECT_CAP


def mutation_violation(total_events, object_name, object_count, type_id):
    """The reason to stop a runaway task, or None to let it keep working.

    Split out of the GUI so it can be tested with the numbers real builds
    produce. Both false positives this guard has caused were only discovered by
    a user losing several minutes of correct work to a rollback, which is a poor
    substitute for a test.
    """
    if total_events > TOTAL_MUTATION_CAP:
        return "Task exceeded %d live document mutation events" % TOTAL_MUTATION_CAP
    cap = object_event_cap(type_id)
    if object_name and object_count > cap:
        return "Task repeatedly changed %s more than %d times" % (object_name, cap)
    return None


def evaluate_guard(elapsed_seconds, budget_seconds, rss_growth_bytes,
                   missing_checks, document_missing=False, path_changed=False,
                   canonical_missing=False, discovery_error=None,
                   memory_limit_bytes=RSS_GROWTH_LIMIT_BYTES):
    """One tick of the file guard / watchdog.

    Returns ``(violation, missing_checks, restore_file)``:
      * ``violation`` — the reason string that ends the task, or None,
      * ``missing_checks`` — the updated consecutive-miss counter (caller stores it),
      * ``restore_file`` — True when the in-memory FCStd backup must be written back.

    Note the deliberate asymmetry, preserved from the original inline code:
    document-level violations (closed, moved, deleted) OVERWRITE a discovery
    error, because they are the more specific and more serious finding; the
    budget and memory limits only apply when nothing else already fired.
    """
    violation = None
    if discovery_error:
        violation = "Bridge discovery could not be maintained: %s" % discovery_error

    if document_missing:
        violation = "Backend closed the protected FreeCAD document"
    elif path_changed:
        violation = "Backend changed the protected document path"

    restore_file = False
    if canonical_missing:
        missing_checks = missing_checks + 1
        if missing_checks >= MISSING_FILE_TOLERANCE:
            restore_file = True
            violation = "Backend removed the protected FCStd file; in-memory backup was restored"
    else:
        missing_checks = 0

    if elapsed_seconds > budget_seconds:
        violation = violation or ("Task exceeded its %d minute live-editing budget"
                                  % max(1, int(budget_seconds) // 60))
    elif rss_growth_bytes > memory_limit_bytes:
        violation = violation or "FreeCAD memory grew by more than 1 GiB during the task"

    return violation, missing_checks, restore_file


def classify_outcome(exit_code, changed, validation_ok, bridge_events,
                     observed_mutations=None, assistant_text=""):
    """Decide whether a finished backend run counts as delivered.

    A normal modeling task succeeds only on hard evidence: the backend exited
    cleanly, the document fingerprint changed, geometry validates, and at least
    one mutation actually came through the bridge.

    ``observed_mutations`` is the document observer's mutation counter for this
    task (None when unavailable, e.g. metrics belonged to another task). It —
    not the fingerprint — is the trustworthy "did anything really change"
    signal: ``Shape.hashCode()`` jitters on a bare recompute because
    still-touched features rebuild every time, which once made a successful
    slice-only task read as "changed".

    Returns ``{"status", "action_only", "follow_up"}``.
    """
    success = bool(exit_code == 0 and changed and validation_ok and bridge_events > 0)

    # An action-only task (slice / print / status query) legitimately leaves the
    # document alone, so it can never satisfy the "changed" test above. It is
    # accepted only when the worker explicitly claimed it with the marker AND
    # nothing was really mutated.
    genuinely_unchanged = (not changed) or observed_mutations == 0
    action_only = bool(not success and exit_code == 0 and genuinely_unchanged
                       and validation_ok
                       and ACTION_ONLY_MARKER in (assistant_text or ""))
    if action_only:
        success = True

    if action_only:
        # Nothing to commit or snapshot: just close the empty transaction.
        follow_up = ABORT_TRANSACTION
    elif success:
        follow_up = COMMIT_AND_SAVE
    elif changed and (observed_mutations is None or observed_mutations > 0):
        # A real but unverified mutation is the dangerous case — roll it back.
        follow_up = RESTORE_SNAPSHOT
    else:
        follow_up = ABORT_TRANSACTION

    return {
        "status": "success" if success else "failed",
        "action_only": action_only,
        "follow_up": follow_up,
    }
