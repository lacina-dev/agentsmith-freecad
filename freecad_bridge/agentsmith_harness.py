"""Assembly of the modeling-harness prompt from harness/registry.json + *.md.

Extracted from BridgeGui so it can be unit-tested without FreeCAD or Qt, and so
the panel and the eval runner assemble the SAME text. They used to hold two
copies of this logic and had already drifted (the eval copy was missing a
sentence of the index preamble), which meant the eval harness measured a
slightly different prompt than production ever sends.

This module MUST NOT import FreeCAD, FreeCADGui, Part or PySide.
Module name is prefixed because every FreeCAD Mod directory shares sys.path.
"""

import json
import os

INDEX_HEADING = "## Harness library — apply what fits"
INDEX_PREAMBLE = (
    "All playbooks below are ALWAYS provided; there is no single mode. Apply every "
    "playbook whose trigger matches the part's character or how it will be used, and "
    "ignore the ones that don't apply. Usually several apply at once (e.g. a printed "
    "load-bearing bracket → parametrics + strength + print3d + verify)."
)


#: Where the playbooks live. Defined here rather than in the panel: two modules
#: needed it and each had grown its own copy, which is how two paths that must be
#: identical start drifting.
HARNESS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness")


def load_registry(harness_dir, warn=None):
    """Read registry.json. A missing or broken registry degrades to an empty one
    rather than breaking task launch — a task with no harness is still better
    than no task at all.

    Playbook entries without both ``id`` and ``file`` are dropped.
    """
    registry = {"always_include": [], "playbooks": []}
    try:
        with open(os.path.join(harness_dir, "registry.json"), "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            registry["always_include"] = list(loaded.get("always_include", []))
            registry["playbooks"] = [p for p in loaded.get("playbooks", [])
                                     if p.get("id") and p.get("file")]
    except Exception as exc:
        if warn:
            warn("Harness registry warning: %s" % exc)
    return registry


def build_index(playbooks):
    """The generated table of contents: every playbook with its trigger, so the
    agent can select the applicable ones itself. Returns "" for no playbooks."""
    if not playbooks:
        return ""
    lines = [INDEX_HEADING, INDEX_PREAMBLE]
    for playbook in playbooks:
        trigger = playbook.get("trigger")
        label = playbook.get("label", playbook["id"])
        lines.append("- **%s** — apply when %s" % (label, trigger) if trigger
                     else "- **%s**" % label)
    return "\n".join(lines)


def assemble_harness(harness_dir, label_template="úplný harness (%d playbooků)",
                     warn=None, registry=None):
    """Assemble the full harness text: always-included files, then the generated
    index, then every playbook body.

    There is no exclusive mode — the whole library always goes to the backend and
    the agent applies what fits. Returns ``(label, text)``; an unreadable file
    contributes an empty section instead of aborting.
    """
    if registry is None:
        registry = load_registry(harness_dir, warn)
    playbooks = registry.get("playbooks", [])

    def _read(name):
        try:
            with open(os.path.join(harness_dir, name), "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except Exception as exc:
            if warn:
                warn("Harness file warning (%s): %s" % (name, exc))
            return ""

    sections = [_read(name) for name in registry.get("always_include", [])]
    index = build_index(playbooks)
    if index:
        sections.append(index)
        sections.extend(_read(playbook["file"]) for playbook in playbooks)

    label = label_template % len(playbooks)
    return label, "\n\n".join(section for section in sections if section)


# --------------------------------------------------------------------------- #
# Tools copied into the task's working directory
# --------------------------------------------------------------------------- #
#: The agent runs from the project folder and the prompts reference these by
#: relative path, so the addon drops its own copies there.
AGENT_TOOLS = ("freecad_bridge_client.py", "slice_check.py", "print_send.py",
               "slicer-config.json")

#: Copied every single time, never compared by timestamp. The tools locate the
#: config NEXT TO THEMSELVES, so any stale copy in the project silently shadows
#: the real one -- and the agent edits this file during a run, which makes the
#: stale copy the newer of the two and defeats a timestamp check exactly when it
#: matters. Live consequence: a task was told the printer had no PLA profile and
#: no network address, hours after both were configured, and it correctly
#: refused to print rather than guess.
ALWAYS_REFRESHED = ("slicer-config.json",)


def should_refresh(name, target_mtime, source_mtime):
    """Does the project's copy of an agent tool need rewriting?

    `target_mtime` is None when the file is absent. Timestamps decide for the
    executable tools -- copy2 preserves the source mtime, so an up-to-date copy
    is left alone and a genuinely newer local edit is respected -- but the config
    is authoritative in the addon and is always rewritten.
    """
    if target_mtime is None:
        return True
    if name in ALWAYS_REFRESHED:
        return True
    return target_mtime < source_mtime

