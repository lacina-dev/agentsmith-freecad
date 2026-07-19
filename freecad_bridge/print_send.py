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
# PrusaLink (Prusa CORE One)
# --------------------------------------------------------------------------- #
def prusalink_status(host, network):
    request = urllib.request.Request(
        "http://%s/api/v1/status" % host,
        headers={"X-Api-Key": network.get("api_key", "")})
    status, body = _http(request)
    if status == 401:
        raise PrinterError("PrusaLink rejected the API key (401). Set printers.<key>.network.api_key.")
    if status >= 400:
        raise PrinterError("PrusaLink /status returned HTTP %d: %s" % (status, body[:300]))
    return json.loads(body.decode("utf-8", "replace"))


def prusalink_send(host, network, gcode_path, start):
    name = os.path.basename(gcode_path)
    with open(gcode_path, "rb") as handle:
        payload = handle.read()
    headers = {
        "X-Api-Key": network.get("api_key", ""),
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(payload)),
        "Overwrite": "?1",
    }
    if start:
        headers["Print-After-Upload"] = "?1"
    request = urllib.request.Request(
        "http://%s/api/v1/files/usb/%s" % (host, urllib.parse.quote(name)),
        data=payload, headers=headers, method="PUT")
    status, body = _http(request)
    if status == 401:
        raise PrinterError("PrusaLink rejected the API key (401).")
    if status >= 400:
        raise PrinterError("PrusaLink upload failed (HTTP %d): %s" % (status, body[:300]))
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
            result = send_fn(host, network, gcode, opts.start)
            result.update({"printer": printer["_key"], "network": kind})
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
