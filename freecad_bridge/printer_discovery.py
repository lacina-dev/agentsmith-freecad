#!/usr/bin/env python3
"""printer_discovery.py -- find 3D printers on the local network.

Two ways to find a machine, deliberately not equal:

* **mDNS (default).** One multicast question on the local link. Nothing is aimed
  at any individual host and nothing leaves the machine. This is what the "add a
  printer" dialog of every operating system does, and it is safe to run on
  anyone's network without asking.
* **Subnet probe (opt-in only).** Opening a TCP connection to every address in
  the local /24 is a port scan. On a home network it is harmless; on a corporate
  one it trips intrusion detection and the user gets to explain themselves to
  their network administrator. This module will do it, but only when a caller
  explicitly asks -- never as part of the default path.

Discovery answers "is something there", which is NOT the same as "can I use it".
Those are tracked as separate states (see PrinterState) because collapsing them
into one green lamp would report a printer we cannot authenticate against as
ready to print.

Stdlib only: FreeCAD ships its own Python and users cannot be asked to pip
install anything into it.
"""

import json
import os
import socket
import struct
import sys
import time

# --------------------------------------------------------------------------- #
# What we know how to talk to
# --------------------------------------------------------------------------- #
# Service type -> the network dialect print_send.py/slice_check.py already speak.
SERVICE_TYPES = {
    "_prusalink._tcp.local": "prusalink",
    "_octoprint._tcp.local": "octoprint",
    "_moonraker._tcp.local": "moonraker",
}

# Ports worth trying during an explicit subnet probe, and what usually answers.
PROBE_PORTS = ((80, "prusalink"), (7125, "moonraker"), (5000, "octoprint"))

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
ENUMERATION_NAME = "_services._dns-sd._udp.local"


# --------------------------------------------------------------------------- #
# States -- four independent facts, not one "available" flag
# --------------------------------------------------------------------------- #
class PrinterState(object):
    FOUND = "found"              # something announced itself / answered a probe
    RESPONDING = "responding"    # HTTP answers, but we are not authenticated
    CONNECTED = "connected"      # the credential works; we can read the machine
    UNREACHABLE = "unreachable"  # configured, but nothing is there right now
    CHECKING = "checking"        # remembered, and we are asking it right now


#: Human-readable, kept next to the constants so the UI and the CLI agree.
STATE_LABELS = {
    PrinterState.FOUND: "found",
    PrinterState.RESPONDING: "answers (no key)",
    PrinterState.CONNECTED: "connected",
    PrinterState.UNREACHABLE: "unreachable",
    PrinterState.CHECKING: "checking…",
}


# --------------------------------------------------------------------------- #
# Minimal DNS wire format
# --------------------------------------------------------------------------- #
TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV = 1, 12, 16, 33


def encode_name(name):
    return b"".join(bytes([len(p)]) + p.encode("utf-8")
                    for p in name.split(".") if p) + b"\x00"


def read_name(buf, offset, _depth=0):
    """Decode a DNS name, following compression pointers.

    Pointers are why a hand-rolled 'pull out the printable bits' parser fails on
    real traffic: every responder compresses, so the interesting half of each
    record is a two-byte back-reference rather than text.
    """
    if _depth > 12:                       # a pointer loop must not hang the UI
        raise ValueError("compression pointer loop")
    parts = []
    while True:
        if offset >= len(buf):
            raise ValueError("truncated name")
        length = buf[offset]
        if length == 0:
            return ".".join(parts), offset + 1
        if length & 0xC0 == 0xC0:
            pointer = struct.unpack("!H", buf[offset:offset + 2])[0] & 0x3FFF
            suffix, _ = read_name(buf, pointer, _depth + 1)
            parts.append(suffix)
            return ".".join(parts), offset + 2
        parts.append(buf[offset + 1:offset + 1 + length].decode("utf-8", "replace"))
        offset += 1 + length


def parse_records(buf):
    """Yield (rtype, name, value) for every record in a DNS/mDNS message."""
    if len(buf) < 12:
        return []
    counts = struct.unpack("!4H", buf[4:12])
    offset, records = 12, []
    for _ in range(counts[0]):                       # questions
        _, offset = read_name(buf, offset)
        offset += 4
    for _ in range(sum(counts[1:])):                 # answers + authority + extra
        name, offset = read_name(buf, offset)
        if offset + 10 > len(buf):
            break
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", buf[offset:offset + 10])
        offset += 10
        rdata, end = buf[offset:offset + rdlen], offset + rdlen
        value = None
        try:
            if rtype == TYPE_PTR:
                value = read_name(buf, offset)[0]
            elif rtype == TYPE_SRV:
                port = struct.unpack("!H", rdata[4:6])[0]
                value = (read_name(buf, offset + 6)[0], port)
            elif rtype == TYPE_A and len(rdata) == 4:
                # len(rdata), not rdlen: a truncated packet declares four bytes
                # it does not carry, and inet_ntoa answers that with OSError.
                value = socket.inet_ntoa(rdata)
            elif rtype == TYPE_TXT:
                pairs, i = {}, 0
                while i < len(rdata):
                    item = rdata[i + 1:i + 1 + rdata[i]].decode("utf-8", "replace")
                    key, _, val = item.partition("=")
                    pairs[key] = val
                    i += 1 + rdata[i]
                value = pairs
        except (ValueError, struct.error, IndexError, OSError):
            value = None
        offset = end
        if value is not None:
            records.append((rtype, name, value))
    return records


# --------------------------------------------------------------------------- #
# mDNS discovery
# --------------------------------------------------------------------------- #
def open_mdns_socket():
    """A socket that hears the multicast replies, degrading gracefully.

    Port 5353 is usually already held by avahi (Linux) or Bonjour (macOS/Windows).
    Sharing it is what lets us see answers sent to the group; when the OS refuses,
    an ephemeral port still catches the unicast half rather than failing outright.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    for option in ("SO_REUSEPORT",):
        if hasattr(socket, option):
            try:
                sock.setsockopt(socket.SOL_SOCKET, getattr(socket, option), 1)
            except OSError:
                pass
    try:
        sock.bind(("", MDNS_PORT))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        struct.pack("4sl", socket.inet_aton(MDNS_GROUP),
                                    socket.INADDR_ANY))
    except OSError:
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    except OSError:
        pass
    return sock


def send_query(sock, name, qtype=TYPE_PTR):
    query = (struct.pack("!6H", 0, 0, 1, 0, 0, 0) + encode_name(name)
             + struct.pack("!2H", qtype, 1))
    try:
        sock.sendto(query, (MDNS_GROUP, MDNS_PORT))
        return True
    except OSError:
        return False


def collect_mdns_packets(service_names, timeout=4.0, sock=None):
    """Ask the link for services and return the raw replies.

    Split from parsing so the parser can be tested against captured traffic --
    the wire format is where the bugs live, and it must not need a live printer
    to exercise.
    """
    own = sock is None
    if own:
        sock = open_mdns_socket()

    packets = []
    try:
        for name in service_names:
            send_query(sock, name)
        sock.settimeout(0.5)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            packets.append((addr[0], data))
    finally:
        if own:
            sock.close()
    return packets


def printers_from_packets(packets):
    """Turn captured mDNS traffic into printer entries.

    A single machine's answer is spread across record types -- PTR names the
    instance, SRV gives host and port, A resolves the host -- and the records may
    arrive in separate packets, so everything is accumulated before matching.
    """
    instances, srv, addresses, txt = {}, {}, {}, {}
    for source, data in packets:
        try:
            records = parse_records(data)
        except (ValueError, struct.error, IndexError, OSError):
            continue                      # a malformed reply must not kill the scan
        for rtype, name, value in records:
            if rtype == TYPE_PTR and value in SERVICE_TYPES:
                continue                  # enumeration answer: a type, not an instance
            if rtype == TYPE_PTR:
                service = name.lower()
                if service in SERVICE_TYPES:
                    instances[value] = (SERVICE_TYPES[service], source)
            elif rtype == TYPE_SRV:
                srv[name] = value
            elif rtype == TYPE_A:
                addresses[name] = value
            elif rtype == TYPE_TXT:
                txt[name] = value

    found = []
    for instance, (kind, source) in sorted(instances.items()):
        host, port = srv.get(instance, (None, None))
        address = addresses.get(host) if host else None
        found.append({
            "kind": kind,
            "label": instance.split(".")[0].replace("_", " "),
            "hostname": host,
            "port": port,
            "address": address or source,
            "txt": txt.get(instance, {}),
            "via": "mdns",
        })
    return found


def discover_mdns(timeout=6.0, sock=None, services=None):
    """The default, safe discovery path: listen to what announces itself.

    One question per round, not one burst. Measured against a live Prusa CORE
    One: a packet carrying several questions gets exactly the first one answered
    and the rest ignored, which looks identical to "no printers here". Asking
    each service type separately answered 3 times out of 3.

    (The RFC 6762 unicast-response bit looked like the tidy fix for a one-shot
    querier and was tried first -- it returned nothing at all from this printer,
    so the plain multicast question stands.)

      1. one round per printer service type we understand
      2. resolve each instance found: SRV -> host:port, A -> address
    """
    own = sock is None
    sock = sock or open_mdns_socket()
    services = list(services or SERVICE_TYPES)
    per_round = max(1.0, timeout / (len(services) + 2.0))
    try:
        packets = []
        # Roughly one query in four goes unanswered on a quiet network, so a
        # single silent round is not evidence of an empty network. Ask again
        # before concluding that; a printer vanishing from the panel reads as
        # "it is switched off", which is a worse lie than a slower refresh.
        for attempt in range(2):
            for service in services:
                send_query(sock, service)
                packets += collect_mdns_packets([], per_round, sock=sock)
            if any(rtype == TYPE_PTR and name.lower() in SERVICE_TYPES
                   for rtype, name, _v in _safe_records(packets)):
                break

        instances = {value for rtype, name, value in _safe_records(packets)
                     if rtype == TYPE_PTR and name.lower() in SERVICE_TYPES}
        missing = instances - {name for rtype, name, _v in _safe_records(packets)
                               if rtype == TYPE_SRV}
        for instance in sorted(missing):
            send_query(sock, instance, TYPE_SRV)
            packets += collect_mdns_packets([], per_round, sock=sock)

        hosts = {value[0] for rtype, _n, value in _safe_records(packets)
                 if rtype == TYPE_SRV}
        unresolved = hosts - {name for rtype, name, _v in _safe_records(packets)
                              if rtype == TYPE_A}
        for host in sorted(unresolved):
            send_query(sock, host, TYPE_A)
            packets += collect_mdns_packets([], per_round, sock=sock)

        return printers_from_packets(packets)
    finally:
        if own:
            sock.close()


def _safe_records(packets):
    """Every record across captured packets; malformed replies are skipped.

    One badly-formed answer from an unrelated device on the network must not be
    able to hide every printer behind it.
    """
    for _source, data in packets:
        try:
            for record in parse_records(data):
                yield record
        except (ValueError, struct.error, IndexError, OSError):
            continue


# --------------------------------------------------------------------------- #
# Bambu Lab -- SSDP on its own port
# --------------------------------------------------------------------------- #
BAMBU_GROUP = "239.255.255.250"
BAMBU_PORTS = (2021, 1990)          # Bambu announces on 2021; 1990 on some firmware

#: NOT VERIFIED AGAINST REAL HARDWARE -- there is no Bambu on this network. The
#: parser is written to the documented announcement format and tested against a
#: synthetic packet in exactly that shape; treat a first run with a real printer
#: as the actual test. Everything else here degrades to "found nothing", which is
#: the safe direction to be wrong in.
BAMBU_HEADERS = {
    "location": "address",
    "usn": "serial",
    "devmodel.bambu.com": "model",
    "devname.bambu.com": "label",
}


def parse_bambu_announcement(text):
    """Pull a printer out of an SSDP NOTIFY, or return None if it is not one."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or not lines[0].upper().startswith(("NOTIFY", "HTTP/1.1")):
        return None
    fields = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    if "bambulab" not in " ".join(fields.values()).lower() \
            and not any(k.endswith("bambu.com") for k in fields):
        return None
    entry = {"kind": "bambu", "via": "ssdp", "txt": {}, "port": None,
             "hostname": None, "kind_verified": True}
    for header, field in BAMBU_HEADERS.items():
        if fields.get(header):
            entry[field] = fields[header]
    entry.setdefault("label", entry.get("model") or entry.get("address") or "Bambu")
    if not entry.get("address"):
        return None
    return entry


def discover_bambu(timeout=4.0, ports=BAMBU_PORTS):
    """Listen for Bambu printers announcing themselves.

    Passive: Bambu machines broadcast periodically on their own, so no query is
    sent. Finding one is genuinely useful for the list, but AgentSmith cannot
    drive it -- the LAN interface is MQTT over TLS plus FTPS, not the HTTP APIs
    this addon speaks -- so such a printer is listed and honestly marked as
    unsupported rather than shown with a Print button.
    """
    found, seen = [], set()
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", port))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            struct.pack("4sl", socket.inet_aton(BAMBU_GROUP),
                                        socket.INADDR_ANY))
        except OSError:
            sock.close()
            continue                 # port held by something else; not fatal
        sock.settimeout(0.5)
        deadline = time.time() + (timeout / len(ports))
        try:
            while time.time() < deadline:
                try:
                    data, _addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                entry = parse_bambu_announcement(data.decode("utf-8", "replace"))
                if entry and entry["address"] not in seen:
                    seen.add(entry["address"])
                    entry["note"] = ("Bambu announces itself, but AgentSmith cannot "
                                     "drive it yet (MQTT/FTPS, not an HTTP API).")
                    entry["state"] = PrinterState.FOUND
                    found.append(entry)
        finally:
            sock.close()
    return found


# --------------------------------------------------------------------------- #
# Active subnet probe -- explicit opt-in only
# --------------------------------------------------------------------------- #
def local_ipv4():
    """This machine's address on the network it would use to reach a printer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))   # TEST-NET-1: routable nowhere, sends nothing
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def subnet_hosts(address, prefix=24):
    """Addresses of the /24 around `address`, excluding network and broadcast."""
    if not address or prefix != 24:
        return []
    head = address.rsplit(".", 1)[0]
    return ["%s.%d" % (head, n) for n in range(1, 255)]


def probe_port(address, port, timeout=0.35):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((address, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()

SCAN_WORKERS = 64


def scan_subnet(address=None, timeout=0.35, ports=PROBE_PORTS, progress=None,
                _probe=probe_port, workers=SCAN_WORKERS):
    """Knock on every address in the local /24. THIS IS A PORT SCAN.

    Callers must have obtained explicit consent for this; it exists for networks
    that block mDNS between segments, not as a default. An open port is reported
    as a candidate only -- what actually listens there is decided by identify().

    Concurrent because sequential is unusable: 254 addresses times three ports at
    a third of a second each is over four minutes of a frozen panel, and a check
    nobody is willing to wait for is a check nobody runs.
    """
    from concurrent.futures import ThreadPoolExecutor

    address = address or local_ipv4()
    hosts = [h for h in subnet_hosts(address) if h != address]
    if not hosts:
        return []

    cancelled = [False]

    def knock(host):
        if cancelled[0]:
            return None
        open_ports = [p for p, _guess in ports if _probe(host, p, timeout)]
        if not open_ports:
            return None
        # Every open port is kept: a Klipper machine serves its web UI on 80 and
        # its API on 7125, and stopping at the first hit would have us knocking
        # on the door of a printer while calling it by another manufacturer's name.
        guess = dict((p, g) for p, g in ports)[open_ports[0]]
        return {"kind": guess, "label": host, "hostname": None,
                "port": open_ports[0], "ports": open_ports, "address": host,
                "txt": {}, "via": "scan", "kind_verified": False}

    # Submitted in batches rather than all at once: handing the whole subnet to
    # the pool up front means a cancel arrives after every address has already
    # been knocked on, which makes the stop button decorative.
    found, done = [], 0
    width = min(workers, len(hosts))
    with ThreadPoolExecutor(max_workers=width) as pool:
        for start in range(0, len(hosts), width):
            if cancelled[0]:
                break
            for result in pool.map(knock, hosts[start:start + width]):
                if result:
                    found.append(result)
                done += 1
            if progress and not progress(done, len(hosts)):
                cancelled[0] = True        # the user cancelled; stop knocking
    return sorted(found, key=lambda e: [int(p) for p in e["address"].split(".")])


# --------------------------------------------------------------------------- #
# Identification and state
# --------------------------------------------------------------------------- #
#: How each dialect answers when asked who it is. The port is the usual one;
#: `recognise` gets the decoded JSON and says whether this really is that thing.
DIALECTS = (
    ("moonraker", 7125, "/printer/info",
     lambda d: isinstance(d.get("result"), dict)
     and ("klipper_path" in d["result"] or "state" in d["result"])),
    ("prusalink", 80, "/api/version",
     lambda d: "PrusaLink" in str(d.get("text", "")) or "nozzle_diameter" in d),
    ("octoprint", 5000, "/api/version",
     lambda d: "OctoPrint" in str(d.get("text", ""))),
)


def identify(entry, api_key=None, opener=None):
    """Decide what a machine is and which of the four states it is in.

    Two rules, both learned from getting it wrong against real hardware:

    A port is a hint, not an identity. A QIDI X-Max 3 has port 80 open and was
    duly announced as a Prusa until the answer itself was read -- so a scanned
    host is asked every dialect we speak and believed only when the reply looks
    like that dialect. A machine that announced itself over mDNS is different:
    the service type IS the answer, so it is trusted and only confirmed.

    A 401 means "there is a printer here and I cannot read it": that is
    RESPONDING, never CONNECTED. Reporting an unauthenticated machine as ready
    is the same class of error as calling an unverified nozzle a matching one.
    """
    from urllib import request as urlrequest

    opener = opener or (lambda req: _read(urlrequest, req))
    address = entry.get("address")
    if not address:
        return dict(entry, state=PrinterState.UNREACHABLE)

    if entry.get("kind") == "bambu":
        # Nothing to probe: its LAN interface is not HTTP. The announcement is
        # all the evidence there is, and claiming more would be invention.
        return dict(entry, state=entry.get("state", PrinterState.FOUND),
                    kind_verified=True)

    trusted = entry.get("via") == "mdns" and entry.get("kind") in dict(
        (name, True) for name, _p, _path, _r in DIALECTS)
    open_ports = entry.get("ports") or ([entry["port"]] if entry.get("port") else [])

    candidates = []
    for name, default_port, path, recognise in DIALECTS:
        if trusted and name != entry.get("kind"):
            continue
        for port in ([entry.get("port") or default_port] if trusted
                     else _ports_to_try(default_port, open_ports)):
            candidates.append((name, port, path, recognise))

    # Not every non-answer is equally uninformative. A guarded 401 says a service
    # is there and defends itself; a 404 says only that something serves HTTP.
    # Without this ranking the first dialect asked wins by arriving first, and a
    # locked printer gets buried under a 404 from an unrelated port.
    strength = {PrinterState.FOUND: 1, PrinterState.RESPONDING: 2}

    def keep(current, candidate):
        if current is None:
            return candidate
        return candidate if strength.get(candidate[2], 0) > \
            strength.get(current[2], 0) else current

    result = dict(entry)
    best = None
    for name, port, path, recognise in candidates:
        host = address if port in (80, None) else "%s:%d" % (address, port)
        headers = {"X-Api-Key": api_key} if api_key else {}
        try:
            status, body = opener(urlrequest.Request(
                "http://%s%s" % (host, path), headers=headers))
        except Exception:
            continue
        if status in (401, 403):
            # Something is listening and guarding itself. Remember it, but keep
            # looking: another dialect may answer openly and name the machine.
            best = keep(best, (name, port, PrinterState.RESPONDING,
                               "needs an API key", {}))
            continue
        if status >= 400:
            best = keep(best, (name, port, PrinterState.FOUND,
                               "answered HTTP %d" % status, {}))
            continue
        try:
            info = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            info = {}
        if not isinstance(info, dict):
            info = {}
        if recognise(info) or trusted:
            state = PrinterState.CONNECTED if (api_key or name == "moonraker") \
                else PrinterState.RESPONDING
            best = (name, port, state, None, info)
            break
        best = keep(best, (name, port, PrinterState.FOUND,
                           "answers, but is not recognised", {}))

    if best is None:
        result["state"] = PrinterState.UNREACHABLE
        return result

    name, port, state, note, info = best
    verified = bool(info) or entry.get("via") == "mdns"
    result["port"] = port
    result["kind_verified"] = verified
    result["state"] = state
    if verified:
        result["kind"] = name
    elif state == PrinterState.RESPONDING:
        # Guarded by a key, so we cannot read what it is. The guess is kept
        # because a locked printer is still probably a printer, but it is
        # labelled a guess so nobody prints to it on the strength of a port.
        result["kind"] = name
        note = "needs an API key — type unverified"
    else:
        # A web server that answers with HTML is a router, a NAS, a doorbell.
        # Naming it after whichever dialect we happened to ask first is how a
        # router ended up in the printer list; unknown is the honest answer.
        result["kind"] = None
        note = "unknown device — does not answer like a printer"
    if note:
        result["note"] = note
    payload = info.get("result") if isinstance(info.get("result"), dict) else info
    for key in ("hostname", "printer", "firmware", "nozzle_diameter", "text",
                "software_version", "state_message"):
        if key in payload:
            result[key] = payload[key]
    if payload.get("hostname"):
        result["label"] = payload["hostname"]
    return result


def _ports_to_try(default_port, open_ports):
    """Ports worth asking, preferring ones a scan actually found open."""
    ordered = [p for p in open_ports if p == default_port]
    ordered += [p for p in open_ports if p != default_port]
    if not ordered:
        ordered = [default_port]
    return ordered


def _read(urlrequest, req, timeout=3.0):
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            return response.getcode(), response.read()
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code is None:
            raise
        body = b""
        try:
            body = exc.read()
        except Exception:
            pass
        return code, body


# --------------------------------------------------------------------------- #
# Reconciling discovery with what is configured
# --------------------------------------------------------------------------- #
def config_signature(printer):
    """The address a configured printer would be discovered at, if reachable."""
    network = printer.get("network") or {}
    host = (network.get("host") or "").strip()
    return host.rsplit(":", 1)[0] if host else None


#: Facts owned by the remembered-printer book, not by discovery. merge_with_config
#: rebuilds rows from scratch, so without this list the user's own name, the
#: identity verdict and the "we know this machine" flag were silently dropped on
#: the way to the panel.
BOOK_KEYS = ("book_id", "remembered", "last_seen", "identity", "kind_verified")


def _carry(row, entry):
    for key in BOOK_KEYS:
        if entry.get(key) is not None:
            row[key] = entry[key]
    if entry.get("note") and not row.get("note"):
        row["note"] = entry["note"]
    if entry.get("remembered") and entry.get("label"):
        # The name the user typed outranks the label the config or the machine
        # supplies -- naming a printer is only useful if the name is what shows.
        row["label"] = entry["label"]
    return row


def merge_with_config(config, discovered):
    """Combine configured printers with what is on the network right now.

    Two questions are answered separately on purpose: 'is the machine there'
    (state) and 'can we slice for it' (has a profile). A discovered printer we
    have no slicer profile for is usable for nothing yet, and saying so is more
    useful than a green lamp that lies.
    """
    printers = (config or {}).get("printers") or {}
    by_address = {}
    for entry in discovered:
        if entry.get("address"):
            by_address.setdefault(entry["address"], entry)

    rows, matched = [], set()
    for key, printer in sorted(printers.items()):
        address = config_signature(printer)
        entry = by_address.get(address)
        if entry:
            matched.add(address)
        rows.append(_carry({
            "key": key,
            "label": printer.get("label", key),
            "address": address,
            "kind": (printer.get("network") or {}).get("type"),
            "configured": True,
            "can_slice": bool(printer.get("printer_profile")
                              or printer.get("profile")
                              or printer.get("filaments")),
            "state": (entry or {}).get("state", PrinterState.UNREACHABLE)
            if address else PrinterState.UNREACHABLE,
            "via": (entry or {}).get("via"),
        }, entry or {}))

    for address, entry in sorted(by_address.items()):
        if address in matched:
            continue
        rows.append(_carry({
            "key": None,
            "label": entry.get("label") or address,
            "address": address,
            "kind": entry.get("kind"),
            "configured": False,
            "can_slice": False,          # no profile exists until it is added
            "state": entry.get("state", PrinterState.FOUND),
            "via": entry.get("via"),
            "note": entry.get("note") or ("found, but not in the config"
                                          if not entry.get("remembered") else None),
        }, entry))
    return rows


def configured_entries(config):
    """Discovery candidates built from the config's own addresses.

    A printer whose address we already know does not need to be found -- it
    needs to be asked. Relying on discovery alone reports a perfectly reachable
    machine as unreachable whenever it does not advertise itself, which is the
    normal case for Moonraker (its mDNS announcement is optional and off by
    default).
    """
    entries = []
    for key, printer in ((config or {}).get("printers") or {}).items():
        address = config_signature(printer)
        if not address:
            continue
        network = printer.get("network") or {}
        host = (network.get("host") or "")
        port = None
        if ":" in host:
            try:
                port = int(host.rsplit(":", 1)[1])
            except ValueError:
                port = None
        entries.append({
            "kind": network.get("type"),
            "label": printer.get("label", key),
            "hostname": None,
            "port": port,
            "ports": [port] if port else [],
            "address": address,
            "txt": {},
            "via": "config",
        })
    return entries


def api_key_for(config, address, resolver=None):
    """The credential a configured printer at this address would use, if any.

    Keys live outside the config file (api_key_file / api_key_env) so that the
    tracked JSON never carries a secret; this reuses print_send's resolution so
    there is exactly one place that knows where keys come from.
    """
    for printer in ((config or {}).get("printers") or {}).values():
        if config_signature(printer) != address:
            continue
        network = printer.get("network") or {}
        if resolver is None:
            try:
                import print_send
                resolver = print_send.resolve_api_key
            except Exception:
                return None
        try:
            return resolver(network)
        except Exception:
            return None            # unconfigured key is not an error, just absent
    return None


def add_to_config(row, path=None, label=None):
    """Write a discovered machine into slicer-config.json.

    Only the network half is filled in. The slicer profiles are deliberately left
    empty and `status` stays "unconfigured", so the printer shows up as reachable
    but not sliceable until a human supplies real profiles -- inventing profile
    names would produce G-code for a machine that never had them.
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "slicer-config.json")
    import collections
    try:
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle, object_pairs_hook=collections.OrderedDict)
    except (OSError, ValueError) as exc:
        raise ValueError("The config could not be loaded: %s" % exc)

    key, block = suggest_config_entry(row)
    if label:
        block["label"] = label
    printers = config.setdefault("printers", collections.OrderedDict())
    existing = key
    suffix = 2
    while existing in printers:
        existing = "%s_%d" % (key, suffix)
        suffix += 1
    printers[existing] = block
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
    return existing, block


def load_config(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "slicer-config.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def suggest_config_entry(entry):
    """A printers.<key> block for a discovered machine, ready to be added.

    Deliberately incomplete: no credential is invented and no slicer profile is
    guessed. Both are filled by the user, and the missing profile is what keeps
    can_slice false until someone really sets it up.
    """
    kind = entry.get("kind") or "prusalink"
    address = entry.get("address") or entry.get("hostname") or ""
    port = entry.get("port")
    host = address if port in (80, None) else "%s:%s" % (address, port)
    label = entry.get("label") or address
    key = "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")
    return key or "printer", {
        "label": label,
        "vendor": "Prusa" if kind == "prusalink" else "",
        "network": {
            "type": kind,
            "host": host,
            "api_key_file": "~/.config/agentsmith/%s.key" % (key or "printer"),
        },
        "status": "unconfigured",
        "note": "Discovered on the network. Add a slicer profile and an API key.",
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=4.0,
                        help="how long to listen for mDNS answers (seconds)")
    parser.add_argument("--scan", action="store_true",
                        help="ALSO probe every address in the local /24. This is a "
                             "port scan: fine at home, but it can trip intrusion "
                             "detection on a managed network. Off by default.")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(argv)

    found = discover_mdns(opts.timeout)
    known = {e.get("address") for e in found}
    found += [e for e in discover_bambu(2.0) if e.get("address") not in known]
    if opts.scan:
        known = {e["address"] for e in found}
        found += [e for e in scan_subnet() if e["address"] not in known]

    config = load_config()
    identified = [identify(entry, api_key_for(config, entry.get("address")))
                  for entry in found]
    if opts.json:
        print(json.dumps(identified, indent=2, ensure_ascii=False))
        return 0
    printers = [e for e in identified if e.get("kind")]
    strangers = [e for e in identified if not e.get("kind")]
    for entry in printers:
        mark = "" if entry.get("kind_verified") else "?"
        print("%-16s %-10s%1s %-22s %s" % (
            entry.get("address"), entry.get("kind"), mark,
            STATE_LABELS.get(entry.get("state"), entry.get("state")),
            entry.get("label", "")))
    if strangers:
        print("\n%d other devices answered, but are not printers: %s"
              % (len(strangers), ", ".join(e["address"] for e in strangers)))
    if not printers:
        print("No printer announced itself." + ("" if opts.scan else
              " Try --scan (active scan of the local network)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
