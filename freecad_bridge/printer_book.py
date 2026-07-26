#!/usr/bin/env python3
"""printer_book.py -- the user's own list of printers, remembered across sessions.

Separate from slicer-config.json on purpose. That file ships with the addon and
is tracked in git: it describes the machines the project knows how to slice for.
This one lives in the user's config directory and describes the machines THIS
person owns -- added by hand, named by hand, and kept whether or not they happen
to be reachable from the network the laptop is on right now.

Two rules shape the design:

**A remembered printer never disappears.** Moving a laptop from home to the
office must not empty the list; the printers are still owned, just out of reach.
They stay, marked unreachable, with the date they were last seen.

**An address is not an identity.** 192.168.0.170 is a printer at home and may be
somebody's NAS in another building. So each entry also stores a fingerprint --
what the machine said about itself -- and a remembered printer is only claimed as
present when the thing answering at that address still matches. Lighting up green
for a stranger's device would be worse than not remembering at all.

Stdlib only; no secrets are stored here (API keys keep living in their own files).
"""

import json
import os
import time

BOOK_VERSION = 1
DEFAULT_PATH = os.path.expanduser("~/.config/agentsmith/printers.json")

#: Fields of an /api/version-style reply that identify a specific machine.
#: Firmware version is deliberately NOT among them -- it changes on update, and a
#: printer that updated itself is still the same printer.
FINGERPRINT_KEYS = ("hostname", "printer", "serial", "text")


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def load_book(path=None):
    path = path or DEFAULT_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"version": BOOK_VERSION, "printers": []}
    if not isinstance(data, dict) or not isinstance(data.get("printers"), list):
        # A corrupt book must not take the panel down with it; an empty list is
        # recoverable, a traceback on startup is not.
        return {"version": BOOK_VERSION, "printers": []}
    data.setdefault("version", BOOK_VERSION)
    return data


def save_book(book, path=None):
    path = path or DEFAULT_PATH
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(book, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)          # atomic: a crash mid-write must not lose the list
    return path


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
def fingerprint_of(entry):
    """What a machine said about itself, reduced to the parts that stay put."""
    return {key: str(entry[key]) for key in FINGERPRINT_KEYS
            if entry.get(key) not in (None, "")}


def entry_id(entry):
    """A stable key for a printer, preferring identity over location.

    The address is the last resort precisely because it is the thing that
    changes: DHCP moves a printer, and a laptop that changes networks sees
    entirely different addresses without any printer having moved at all.
    """
    fingerprint = fingerprint_of(entry)
    for key in ("serial", "hostname"):
        if fingerprint.get(key):
            return "%s:%s" % (key, fingerprint[key].lower())
    host = (entry.get("hostname") or "").strip().lower()
    if host:
        return "mdns:%s" % host.rstrip(".")
    return "addr:%s" % (entry.get("address") or "?")


def identity_matches(remembered, observed):
    """Is the machine answering now the one we remember at this address?

    Returns (verdict, reason). An entry we have never fingerprinted answers
    "unknown" rather than "yes" -- there is nothing to compare against, and
    saying so is more honest than assuming the best.
    """
    known = remembered.get("fingerprint") or {}
    seen = fingerprint_of(observed)
    if not known:
        return "unknown", "zatím bez otisku — beru, jak se představila"
    if not seen:
        return "unknown", "stroj se nepředstavil, nemám co porovnat"
    shared = [k for k in known if k in seen]
    if not shared:
        return "unknown", "nemají společný údaj k porovnání"
    for key in shared:
        if known[key].lower() != seen[key].lower():
            return "mismatch", ("na této adrese je něco jiného (%s: %s, čekal jsem %s)"
                                % (key, seen[key], known[key]))
    return "match", None


# --------------------------------------------------------------------------- #
# Editing the book
# --------------------------------------------------------------------------- #
def find(book, printer_id):
    for printer in book.get("printers", []):
        if printer.get("id") == printer_id:
            return printer
    return None


def remember(book, entry, name=None, source="discovered", now=None):
    """Add or update a printer, keeping whatever the user has customised.

    A rediscovery must never overwrite the name the user typed: the panel is
    theirs to label, and "QIDI v dílně" is more useful to them than the mkspi
    hostname the board reports.
    """
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    printer_id = entry_id(entry)
    existing = find(book, printer_id)
    if existing is None:
        # An address collision with a different identity is a rename, not a new
        # machine: the printer we knew here has been re-identified.
        for candidate in book.get("printers", []):
            if candidate.get("address") == entry.get("address") \
                    and candidate.get("id", "").startswith("addr:"):
                existing = candidate
                break

    record = existing if existing is not None else {}
    record["id"] = printer_id
    record["address"] = entry.get("address") or record.get("address")
    record["port"] = entry.get("port") or record.get("port")
    if entry.get("kind"):
        record["kind"] = entry["kind"]
    if entry.get("hostname"):
        record["hostname"] = entry["hostname"]
    fingerprint = fingerprint_of(entry)
    if fingerprint:
        record["fingerprint"] = fingerprint
    if name:
        record["name"] = name
    record.setdefault("name", entry.get("label") or entry.get("address") or "tiskárna")
    record.setdefault("source", source)
    record["last_seen"] = now
    if entry.get("network"):
        record["last_network"] = entry["network"]

    if existing is None:
        book.setdefault("printers", []).append(record)
    return record


def rename(book, printer_id, name):
    printer = find(book, printer_id)
    if printer is None:
        return None
    name = (name or "").strip()
    if name:
        printer["name"] = name
    return printer


def forget(book, printer_id):
    printers = book.get("printers", [])
    remaining = [p for p in printers if p.get("id") != printer_id]
    book["printers"] = remaining
    return len(remaining) != len(printers)


def add_manual(book, address, name=None, port=None, kind=None, now=None):
    """Add a printer the user typed in, before anything is known about it.

    Deliberately allowed without a successful probe: a printer that is switched
    off should still be addable, and the entry simply stays unreachable until it
    answers.
    """
    address = (address or "").strip()
    if not address:
        raise ValueError("prázdná adresa")
    entry = {"address": address, "port": port, "kind": kind, "label": name or address}
    return remember(book, entry, name=name, source="manual", now=now)


# --------------------------------------------------------------------------- #
# Turning the book into discovery candidates
# --------------------------------------------------------------------------- #
def book_entries(book):
    """Remembered printers as probe candidates, wherever we are today."""
    entries = []
    for printer in book.get("printers", []):
        if not printer.get("address"):
            continue
        entries.append({
            "kind": printer.get("kind"),
            "label": printer.get("name") or printer.get("address"),
            "hostname": printer.get("hostname"),
            "port": printer.get("port"),
            "ports": [printer["port"]] if printer.get("port") else [],
            "address": printer["address"],
            "txt": {},
            "via": "book",
            "book_id": printer.get("id"),
        })
    return entries


def annotate(book, identified):
    """Fold what we just observed back onto what we remember.

    The point of the whole module lands here: a remembered printer answering at
    its old address is only reported as itself once the fingerprint agrees. When
    it does not, the row says so instead of quietly presenting a stranger's
    device as the user's printer.
    """
    rows = []
    for entry in identified:
        row = dict(entry)
        printer = find(book, entry.get("book_id") or "") if entry.get("book_id") else None
        if printer is None:
            printer = find(book, entry_id(entry))
        if printer is None:
            rows.append(row)
            continue
        verdict, reason = identity_matches(printer, entry)
        row["book_id"] = printer.get("id")
        row["label"] = printer.get("name") or row.get("label")
        row["remembered"] = True
        row["last_seen"] = printer.get("last_seen")
        row["identity"] = verdict
        if verdict == "mismatch":
            row["state"] = "unreachable"      # not our printer; do not claim it
            row["note"] = reason
        elif reason:
            row["note"] = row.get("note") or reason
        rows.append(row)
    return rows


#: How long a monitored state stays worth quoting. Beyond this the answer is
#: "I do not know any more" -- a printer reported as ready on the strength of a
#: reading from an hour ago is exactly the kind of stale confidence that gets a
#: print started against a machine that is switched off.
STATE_FRESHNESS_SECONDS = 180


def record_state(book, printer_id, state, can_slice=None, now=None):
    """Store what the monitor just observed about one printer."""
    printer = find(book, printer_id)
    if printer is None:
        return None
    printer["last_state"] = state
    printer["last_state_at"] = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    if can_slice is not None:
        printer["can_slice"] = bool(can_slice)
    if state == "connected":
        printer["last_seen"] = printer["last_state_at"]
    return printer


def status_summary(book, now=None, freshness=STATE_FRESHNESS_SECONDS):
    """One line per printer, for the agent's context.

    So that "print it on the Prusa" does not begin with the agent discovering the
    fleet from scratch -- and, more importantly, so that a printer which is
    switched off is known to be switched off before a task plans around it.
    Readings older than `freshness` are reported as unknown rather than quoted.
    """
    now = now or time.time()
    lines = []
    for printer in book.get("printers", []):
        state = printer.get("last_state")
        stamp = printer.get("last_state_at")
        age = None
        if stamp:
            try:
                age = now - time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                age = None
        if state is None or age is None or age > freshness:
            state_text = "stav neznámý"
        else:
            state_text = {"connected": "připojena", "responding": "odpovídá, chybí klíč",
                          "found": "nalezena", "unreachable": "nedostupná",
                          "checking": "stav neznámý"}.get(state, state)
        lines.append("- %s (%s, %s) — %s; slicovat %s" % (
            printer.get("name") or printer.get("address"),
            printer.get("address") or "?",
            printer.get("kind") or "typ neznámý",
            state_text,
            "umím" if printer.get("can_slice") else "neumím (chybí profil)"))
    return "\n".join(lines)


def cached_rows(book):
    """The remembered list, ready to display before anything touches the network.

    The panel used to open empty and stay that way until the user pressed a
    button, which throws away the whole point of remembering: the printers are
    known, only their current state is not. These rows show instantly, marked as
    being checked, and the network fills the state in afterwards.
    """
    rows = []
    for printer in book.get("printers", []):
        rows.append({
            "book_id": printer.get("id"),
            "label": printer.get("name") or printer.get("address"),
            "address": printer.get("address"),
            "kind": printer.get("kind"),
            "state": "checking",
            "remembered": True,
            "configured": False,
            "can_slice": False,
            "last_seen": printer.get("last_seen"),
            "note": "naposledy viděna %s" % (printer.get("last_seen") or "?"),
        })
    return rows


def unseen_rows(book, seen_ids):
    """Printers we remember that did not answer -- listed, never dropped."""
    rows = []
    for printer in book.get("printers", []):
        if printer.get("id") in seen_ids:
            continue
        rows.append({
            "book_id": printer.get("id"),
            "label": printer.get("name") or printer.get("address"),
            "address": printer.get("address"),
            "kind": printer.get("kind"),
            "state": "unreachable",
            "remembered": True,
            "configured": False,
            "can_slice": False,
            "last_seen": printer.get("last_seen"),
            "note": "naposledy viděna %s" % (printer.get("last_seen") or "?"),
        })
    return rows


def current_network():
    """A rough label for where we are, stored for information only.

    Never used to filter the list: the whole point is that printers are
    remembered regardless of which network the machine is on today.
    """
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 9))
            address = sock.getsockname()[0]
        finally:
            sock.close()
        return address.rsplit(".", 1)[0] + ".0/24"
    except OSError:
        return None
