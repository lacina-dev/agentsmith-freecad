#!/usr/bin/env python3
"""print_send.py -- send sliced G-code to a configured printer (AgentSmith fleet).

Companion of slice_check.py. Reads the same slicer-config.json: each printer's
'network' block says how to reach it ('prusalink' for Prusa CORE One,
'moonraker' for Klipper-based machines like the QIDI X-Max 3).

Usage:
    python3 print_send.py --list
    python3 print_send.py --status [--printer KEY]
    python3 print_send.py --send FILE.gcode [--printer KEY] [--start]

Exit codes:
    0  requested action succeeded
    1  printer rejected the request (API error)
    2  setup problem (printer unconfigured, no host, unreachable -- e.g. not at
       home on the printer's network)

Stdlib only. Network hosts are filled in slicer-config.json at home; until
then --status/--send report clearly that the printer is not reachable.
"""

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HTTP_TIMEOUT = 8


class SetupError(Exception):
    """Precondition failed (exit 2)."""


class PrinterError(Exception):
    """The printer API refused the request (exit 1)."""


def resolve_api_key(network):
    """The printer's credential, from wherever it is kept.

    Order: an explicit `api_key`, then the file named by `api_key_file`, then the
    environment variable named by `api_key_env`. The indirection exists because
    slicer-config.json is tracked in git and pushed to a remote — a printer password
    committed there is a password published. Prefer `api_key_file` pointing outside
    the repository (e.g. ~/.config/agentsmith/...), readable only by you.

    On a Prusa CORE One the PrusaLink *password* (Settings -> Network -> PrusaLink)
    is what goes here: the firmware accepts it as X-Api-Key as well as via digest auth.
    """
    direct = (network.get("api_key") or "").strip()
    if direct:
        return direct
    path = network.get("api_key_file")
    if path:
        path = os.path.expanduser(path)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
            if value:
                return value
            raise SetupError("Credential file %s is empty." % path)
        except OSError as exc:
            raise SetupError("Cannot read credential file %s: %s" % (path, exc))
    variable = network.get("api_key_env")
    if variable:
        value = (os.environ.get(variable) or "").strip()
        if value:
            return value
        raise SetupError("Environment variable %s is not set (it should hold the "
                         "printer credential)." % variable)
    return ""


def load_config():
    for path in (os.path.join(HERE, "slicer-config.json"),
                 os.path.join(os.getcwd(), "slicer-config.json")):
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    raise SetupError("slicer-config.json not found (next to print_send.py or in CWD).")


def select_printer(cfg, key):
    printers = cfg.get("printers") or {}
    if not printers:
        raise SetupError("slicer-config.json defines no printers.")
    key = key or cfg.get("default_printer") or next(iter(printers))
    if key not in printers:
        raise SetupError("Printer %r is not configured. Available: %s."
                         % (key, ", ".join(sorted(printers))))
    printer = dict(printers[key])
    printer["_key"] = key
    return printer


def network_of(printer):
    network = printer.get("network") or {}
    kind = (network.get("type") or "").lower()
    host = (network.get("host") or "").strip()
    if not kind:
        raise SetupError(
            "Printer %r has no 'network' block in slicer-config.json — printing to it "
            "is not set up yet (%s)." % (printer["_key"], printer.get("note") or "custom printer"))
    if not host:
        raise SetupError(
            "Printer %r has no network host configured. Fill printers.%s.network.host "
            "in slicer-config.json (done at home, on the printer's network)."
            % (printer["_key"], printer["_key"]))
    return kind, host, network


def _http(request):
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise SetupError(
            "Printer is not reachable (%s). If you are away from its network "
            "(e.g. not at home), the G-code is still ready — send it later." % exc)


# --------------------------------------------------------------------------- #
# Pre-flight: does this G-code match the machine that is about to run it?
# --------------------------------------------------------------------------- #
GCODE_HEADER_BYTES = 65536


def gcode_settings(path):
    """What the slicer baked into the file: nozzle, material, printer model.

    Read from the G-code itself rather than from slicer-config.json, because the
    file on disk is what the printer will execute — a config edited after slicing,
    or a G-code sliced somewhere else entirely, would otherwise pass a check it
    ought to fail.
    """
    patterns = {
        "nozzle_diameter": re.compile(r"^;\s*nozzle_diameter\s*=\s*([\d.]+)", re.I),
        "filament_type": re.compile(r"^;\s*filament_type\s*=\s*(\S+)", re.I),
        "printer_model": re.compile(r"^;\s*printer_model\s*=\s*(.+?)\s*$", re.I),
    }
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(GCODE_HEADER_BYTES)
            if size > GCODE_HEADER_BYTES:
                handle.seek(size - GCODE_HEADER_BYTES)
                text += "\n" + handle.read()
    except OSError as exc:
        raise SetupError("Cannot read G-code %s: %s" % (path, exc))
    found = {}
    for line in text.splitlines():
        for key, pattern in patterns.items():
            if key not in found:
                match = pattern.match(line)
                if match:
                    found[key] = match.group(1).strip()
    return found


def printer_actual(host, network):
    """Nozzle diameter and loaded material as the PRINTER reports them.

    PrusaLink splits these: /api/v1/info carries the nozzle, /api/printer carries
    telemetry.material. A missing value is left missing — never assumed to match.
    """
    actual = {}
    if network.get("type") != "prusalink":
        return actual
    headers = {"X-Api-Key": resolve_api_key(network)}
    for endpoint, extract in (
            ("/api/v1/info", lambda d: ("nozzle_diameter", d.get("nozzle_diameter"))),
            ("/api/printer", lambda d: ("material", (d.get("telemetry") or {}).get("material")))):
        try:
            status, body = _http(urllib.request.Request(
                "http://%s%s" % (host, endpoint), headers=headers))
            if status < 400:
                key, value = extract(json.loads(body.decode("utf-8", "replace")))
                if value is not None:
                    actual[key] = value
        except Exception:
            continue
    return actual


def preflight_mismatches(sliced, actual):
    """(problems, unknowns) — differences that would ruin the print, and gaps.

    A value the printer does not report is NOT counted as a match: "we could not
    check" and "it is fine" are different answers, and only one of them is true.
    """
    problems, unknown = [], []

    sliced_nozzle, actual_nozzle = sliced.get("nozzle_diameter"), actual.get("nozzle_diameter")
    if sliced_nozzle is None:
        unknown.append("the G-code does not state a nozzle diameter")
    elif actual_nozzle is None:
        unknown.append("the printer does not report its nozzle diameter")
    elif abs(float(sliced_nozzle) - float(actual_nozzle)) > 1e-6:
        problems.append("nozzle: sliced for %s mm, printer has %s mm"
                        % (sliced_nozzle, actual_nozzle))

    sliced_material = (sliced.get("filament_type") or "").upper()
    actual_material = (actual.get("material") or "").upper()
    if not sliced_material:
        unknown.append("the G-code does not state a filament type")
    elif actual_material in ("", "---", "UNKNOWN", "NONE"):
        unknown.append("the printer does not report a loaded filament")
    elif sliced_material != actual_material:
        problems.append("filament: sliced for %s, printer has %s"
                        % (sliced_material, actual_material))

    return problems, unknown


# --------------------------------------------------------------------------- #
# PrusaLink (Prusa CORE One)
# --------------------------------------------------------------------------- #
def prusalink_status(host, network):
    request = urllib.request.Request(
        "http://%s/api/v1/status" % host,
        headers={"X-Api-Key": resolve_api_key(network)})
    status, body = _http(request)
    if status == 401:
        raise PrinterError("PrusaLink rejected the credential (401). On a CORE One this is the PrusaLink PASSWORD from Settings -> Network -> PrusaLink; point printers.<key>.network.api_key_file at a file holding it.")
    if status >= 400:
        raise PrinterError("PrusaLink /status returned HTTP %d: %s" % (status, body[:300]))
    return json.loads(body.decode("utf-8", "replace"))


def prusalink_send(host, network, gcode_path, start):
    name = os.path.basename(gcode_path)
    with open(gcode_path, "rb") as handle:
        payload = handle.read()
    headers = {
        "X-Api-Key": resolve_api_key(network),
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(payload)),
        "Overwrite": "?1",
    }
    if start:
        headers["Print-After-Upload"] = "?1"
    url = "http://%s/api/v1/files/usb/%s" % (host, urllib.parse.quote(name))

    def put():
        return _http(urllib.request.Request(
            url, data=payload, headers=headers, method="PUT"))

    status, body = put()
    if status >= 500:
        # PrusaLink stores files on a FAT volume and honours "Overwrite: ?1"
        # unreliably: re-uploading a name that already exists answers with a bare 500,
        # especially once the long name has been folded into an 8.3 alias. Iterating on
        # a model means uploading the same filename repeatedly, so delete first and try
        # again — but only if the delete really succeeded, otherwise the second PUT
        # just reproduces the same 500 and hides the real reason.
        deleted, _ = _http(urllib.request.Request(
            url, headers={"X-Api-Key": headers["X-Api-Key"]}, method="DELETE"))
        if deleted < 400:
            status, body = put()
        elif deleted == 409:
            raise PrinterError(
                "PrusaLink will not replace %s: the printer still has it selected "
                "(DELETE returned 409). Finish or cancel that job on the printer, or "
                "upload under a different filename." % name)
    if status == 401:
        raise PrinterError("PrusaLink rejected the API key (401).")
    if status >= 400:
        hint = ""
        if len(os.path.splitext(name)[0]) > 8:
            hint = (" The name is longer than the 8.3 limit of the printer's USB "
                    "volume; a shorter one (<=8 characters before the extension) "
                    "usually uploads cleanly.")
        raise PrinterError("PrusaLink upload failed (HTTP %d): %s%s"
                           % (status, body[:300], hint))
    return {"uploaded": name, "started": bool(start), "http": status}


# --------------------------------------------------------------------------- #
# Moonraker (Klipper: QIDI X-Max 3 and friends)
# --------------------------------------------------------------------------- #
def _moonraker_base(host):
    return host if ":" in host else host + ":7125"


def moonraker_status(host, network):
    request = urllib.request.Request("http://%s/printer/info" % _moonraker_base(host))
    status, body = _http(request)
    if status >= 400:
        raise PrinterError("Moonraker /printer/info returned HTTP %d: %s" % (status, body[:300]))
    return json.loads(body.decode("utf-8", "replace"))


def moonraker_send(host, network, gcode_path, start):
    name = os.path.basename(gcode_path)
    boundary = uuid.uuid4().hex
    with open(gcode_path, "rb") as handle:
        payload = handle.read()
    parts = []
    for field, value in (("root", "gcodes"), ("print", "true" if start else "false")):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, field, value)).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                  "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name)).encode("utf-8"))
    parts.append(payload)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    body = b"".join(parts)
    request = urllib.request.Request(
        "http://%s/server/files/upload" % _moonraker_base(host),
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary,
                 "Content-Length": str(len(body))},
        method="POST")
    status, response = _http(request)
    if status >= 400:
        raise PrinterError("Moonraker upload failed (HTTP %d): %s" % (status, response[:300]))
    return {"uploaded": name, "started": bool(start), "http": status}


BACKENDS = {
    "prusalink": (prusalink_status, prusalink_send),
    "moonraker": (moonraker_status, moonraker_send),
}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(description="Send G-code to a configured printer.")
    parser.add_argument("--list", action="store_true", help="List printers and their network setup.")
    parser.add_argument("--status", action="store_true", help="Check the printer is reachable.")
    parser.add_argument("--send", default="", help="Path to a .gcode file to upload.")
    parser.add_argument("--printer", default="", help="Printer key from slicer-config.json.")
    parser.add_argument("--force", action="store_true",
                        help="Start the print even if the G-code and the printer disagree "
                             "about nozzle or filament. Wastes filament when you are wrong.")
    parser.add_argument("--start", action="store_true", help="Start the print after upload.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    opts = parser.parse_args(argv)

    try:
        cfg = load_config()
        if opts.list:
            rows = []
            for key, printer in sorted((cfg.get("printers") or {}).items()):
                network = printer.get("network") or {}
                ready = bool((network.get("type") or "") and (network.get("host") or "").strip())
                rows.append({"key": key, "label": printer.get("label", key),
                             "network_type": network.get("type") or "none",
                             "network_ready": ready})
                if not opts.json:
                    state = "network ready" if ready else "network NOT configured (fill at home)"
                    print("%s: %s — %s [%s]" % (key, printer.get("label", key),
                                                state, network.get("type") or "no network block"))
            if opts.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False))
            return 0

        printer = select_printer(cfg, opts.printer.strip() or None)
        kind, host, network = network_of(printer)
        if kind not in BACKENDS:
            raise SetupError("Unknown network type %r for printer %r (supported: %s)."
                             % (kind, printer["_key"], ", ".join(sorted(BACKENDS))))
        status_fn, send_fn = BACKENDS[kind]

        if opts.send:
            gcode = os.path.abspath(os.path.expanduser(opts.send))
            if not os.path.isfile(gcode):
                raise SetupError("G-code file not found: %s" % opts.send)

            # Ask the MACHINE what it is, and compare with what the file was sliced
            # for. Config files describe intent; the printer describes reality, and a
            # 0.6 nozzle running 0.4 G-code (or PETG temperatures into PLA) wastes
            # filament at best. Uploading is reversible, so a mismatch only warns —
            # STARTING a print is not, so that is refused unless --force.
            sliced = gcode_settings(gcode)
            actual = printer_actual(host, network)
            problems, unknown = preflight_mismatches(sliced, actual)
            if not opts.json:
                print("Pre-flight: G-code %s / %s nozzle  vs  printer %s / %s nozzle" % (
                    sliced.get("filament_type", "?"), sliced.get("nozzle_diameter", "?"),
                    actual.get("material", "?"), actual.get("nozzle_diameter", "?")))
                for note in unknown:
                    print("  ? could not verify: %s" % note)
                for note in problems:
                    print("  ! MISMATCH: %s" % note)
            if problems and opts.start and not opts.force:
                raise SetupError(
                    "Refusing to start the print: " + "; ".join(problems)
                    + ". Fix the printer or re-slice; pass --force to override.")

            result = send_fn(host, network, gcode, opts.start)
            result.update({"printer": printer["_key"], "network": kind,
                           "preflight": {"sliced": sliced, "printer": actual,
                                         "mismatches": problems, "unverified": unknown}})
            if opts.json:
                print(json.dumps(result, indent=2))
            else:
                print("Uploaded %s to %s (%s)%s." % (
                    result["uploaded"], printer.get("label", printer["_key"]), kind,
                    " and STARTED the print" if opts.start else " — print not started (use --start)"))
            return 0

        # default / --status: reachability check
        info = status_fn(host, network)
        if opts.json:
            print(json.dumps({"printer": printer["_key"], "network": kind, "reachable": True,
                              "info": info}, indent=2, ensure_ascii=False))
        else:
            print("%s (%s) is reachable at %s." % (printer.get("label", printer["_key"]), kind, host))
        return 0

    except SetupError as exc:
        _emit(opts.json, 2, str(exc))
        return 2
    except PrinterError as exc:
        _emit(opts.json, 1, str(exc))
        return 1


def _emit(as_json, code, message):
    if as_json:
        print(json.dumps({"ok": False, "exit": code, "error": message}))
    else:
        sys.stderr.write("print_send: %s\n" % message)


if __name__ == "__main__":
    sys.exit(main())
