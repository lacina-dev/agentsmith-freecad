"""Tests for finding printers on the local network.

Two things are pinned here, and the second matters more than the first.

The wire format: the mDNS parser is exercised against REAL packets captured from
a live network (tests/fixtures/mdns_responses.jsonl), because every hand-written
guess about DNS-SD framing in this project has so far been wrong -- compression
pointers, the service name (`_prusalink`, not `_prusa-link`), and the fact that a
responder answers one question per packet and ignores the rest.

The honesty of the states: discovery answers "is something there", which is not
"can I use it". A machine that returns 401 has been found, not connected; a
machine with no slicer profile cannot be printed to whatever its network says.
Collapsing either into a single green lamp is the failure these tests exist to
prevent.
"""

import base64
import json
import os
import socket
import struct
import sys
import tempfile
import unittest

import helpers  # noqa: F401

sys.path.insert(0, helpers.ADDON_DIR)
import printer_discovery as pd  # noqa: E402

FIXTURE = os.path.join(helpers.FIXTURES_DIR, "mdns_responses.jsonl")


def captured_packets():
    packets = []
    with open(FIXTURE, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                packets.append((row["from"], base64.b64decode(row["packet"])))
    return packets


def build_packet(records, questions=0):
    """Assemble a DNS response, using compression the way real responders do."""
    body, offsets = b"", {}

    def name_bytes(name):
        if name in offsets:
            return struct.pack("!H", 0xC000 | offsets[name])
        offsets[name] = 12 + len(body)
        return pd.encode_name(name)

    for rtype, name, value in records:
        encoded = name_bytes(name)
        if rtype == pd.TYPE_PTR:
            rdata = pd.encode_name(value)
        elif rtype == pd.TYPE_SRV:
            rdata = struct.pack("!3H", 0, 0, value[1]) + pd.encode_name(value[0])
        elif rtype == pd.TYPE_A:
            rdata = socket.inet_aton(value)
        elif rtype == pd.TYPE_TXT:
            rdata = b"".join(bytes([len(k) + len(v) + 1]) + ("%s=%s" % (k, v)).encode()
                             for k, v in value.items())
        else:
            raise ValueError(rtype)
        body += encoded + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata
    return struct.pack("!6H", 0, 0x8400, questions, len(records), 0, 0) + body


class WireFormatTests(unittest.TestCase):
    def test_captured_traffic_parses(self):
        parsed = [r for _src, data in captured_packets() for r in pd.parse_records(data)]
        self.assertTrue(parsed, "captured fixture produced no records")

    def test_the_live_printer_is_found_in_the_capture(self):
        # The fixture was recorded with a Prusa CORE One on the network.
        names = {str(value) for _t, _n, value in
                 [r for _s, d in captured_packets() for r in pd.parse_records(d)]}
        self.assertTrue(any("prusalink" in n for n in names),
                        "the captured traffic should contain the PrusaLink service")

    def test_compression_pointers_are_followed(self):
        packet = build_packet([
            (pd.TYPE_PTR, "_prusalink._tcp.local", "boxy._prusalink._tcp.local"),
            (pd.TYPE_SRV, "boxy._prusalink._tcp.local", ("boxy.local", 80)),
        ])
        records = pd.parse_records(packet)
        self.assertEqual(records[0][2], "boxy._prusalink._tcp.local")
        self.assertEqual(records[1][2], ("boxy.local", 80))

    def test_a_pointer_loop_does_not_hang(self):
        # A malicious or broken responder must not freeze the panel thread.
        evil = struct.pack("!6H", 0, 0x8400, 0, 1, 0, 0) + struct.pack("!H", 0xC00C)
        evil += struct.pack("!HHIH", pd.TYPE_PTR, 1, 120, 2) + struct.pack("!H", 0xC00C)
        try:
            pd.parse_records(evil)
        except ValueError:
            pass

    def test_truncated_packet_is_rejected_not_crashed(self):
        packet = build_packet([(pd.TYPE_A, "x.local", "10.0.0.5")])
        for cut in range(13, len(packet)):
            try:
                pd.parse_records(packet[:cut])
            except (ValueError, struct.error, IndexError):
                pass

    def test_encode_and_read_name_round_trip(self):
        blob = pd.encode_name("prusa-core-one._prusalink._tcp.local")
        self.assertEqual(pd.read_name(blob, 0)[0], "prusa-core-one._prusalink._tcp.local")


class AssemblyTests(unittest.TestCase):
    """Records for one machine arrive spread across packets and must be joined."""

    def scatter(self):
        return [
            ("192.168.0.170", build_packet([
                (pd.TYPE_PTR, "_prusalink._tcp.local",
                 "prusa-core-one._prusalink._tcp.local")])),
            ("192.168.0.170", build_packet([
                (pd.TYPE_SRV, "prusa-core-one._prusalink._tcp.local",
                 ("prusa-core-one.local", 80))])),
            ("192.168.0.170", build_packet([
                (pd.TYPE_A, "prusa-core-one.local", "192.168.0.170")])),
        ]

    def test_records_from_separate_packets_are_joined(self):
        found = pd.printers_from_packets(self.scatter())
        self.assertEqual(len(found), 1)
        entry = found[0]
        self.assertEqual(entry["kind"], "prusalink")
        self.assertEqual(entry["address"], "192.168.0.170")
        self.assertEqual(entry["port"], 80)
        self.assertEqual(entry["hostname"], "prusa-core-one.local")
        self.assertEqual(entry["via"], "mdns")

    def test_enumeration_answers_are_not_mistaken_for_instances(self):
        # "_prusalink._tcp.local exists on this link" says a TYPE is present,
        # not that a printer instance is named that. Counting it as a printer
        # would put a phantom machine in the panel.
        packets = [("192.168.0.170", build_packet([
            (pd.TYPE_PTR, "_services._dns-sd._udp.local", "_prusalink._tcp.local")]))]
        self.assertEqual(pd.printers_from_packets(packets), [])

    def test_unknown_service_types_are_ignored(self):
        packets = [("192.168.0.5", build_packet([
            (pd.TYPE_PTR, "_androidtvremote2._tcp.local", "tv._androidtvremote2._tcp.local"),
            (pd.TYPE_SRV, "tv._androidtvremote2._tcp.local", ("tv.local", 6466)),
        ]))]
        self.assertEqual(pd.printers_from_packets(packets), [])

    def test_one_broken_packet_does_not_hide_the_others(self):
        packets = self.scatter() + [("192.168.0.9", b"\x00\x01\x02")]
        self.assertEqual(len(pd.printers_from_packets(packets)), 1)

    def test_instance_without_srv_still_reports_the_sender(self):
        # A printer that answers PTR but never resolves is still worth listing;
        # the packet's source address is the best information available.
        packets = [("192.168.0.170", build_packet([
            (pd.TYPE_PTR, "_prusalink._tcp.local", "p._prusalink._tcp.local")]))]
        found = pd.printers_from_packets(packets)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["address"], "192.168.0.170")
        self.assertIsNone(found[0]["port"])


class IdentifyTests(unittest.TestCase):
    """A 401 is not a connection. This is the heart of the honest-state rule."""

    def opener(self, status, body=b"{}"):
        return lambda request: (status, body)

    def entry(self, **kwargs):
        base = {"kind": "prusalink", "address": "10.0.0.7", "port": 80,
                "label": "x", "via": "mdns"}
        base.update(kwargs)
        return base

    def test_unauthenticated_printer_is_responding_not_connected(self):
        result = pd.identify(self.entry(), opener=self.opener(401))
        self.assertEqual(result["state"], pd.PrinterState.RESPONDING)

    def test_forbidden_is_also_only_responding(self):
        result = pd.identify(self.entry(), opener=self.opener(403))
        self.assertEqual(result["state"], pd.PrinterState.RESPONDING)

    def test_working_key_is_connected(self):
        result = pd.identify(self.entry(), api_key="k", opener=self.opener(
            200, b'{"hostname": "prusa-core-one", "nozzle_diameter": 0.4}'))
        self.assertEqual(result["state"], pd.PrinterState.CONNECTED)
        self.assertEqual(result["nozzle_diameter"], 0.4)
        self.assertEqual(result["label"], "prusa-core-one")

    def test_open_api_without_key_is_not_claimed_as_connected(self):
        # PrusaLink answers /api/version without a key on some firmware. Being
        # able to read a version string is not being able to drive the printer.
        result = pd.identify(self.entry(), opener=self.opener(200, b'{"api": "2.0.0"}'))
        self.assertEqual(result["state"], pd.PrinterState.RESPONDING)

    def test_network_failure_is_unreachable(self):
        def boom(request):
            raise OSError("no route to host")
        result = pd.identify(self.entry(), opener=boom)
        self.assertEqual(result["state"], pd.PrinterState.UNREACHABLE)

    def test_garbage_body_does_not_crash_identification(self):
        result = pd.identify(self.entry(), api_key="k",
                             opener=self.opener(200, b"<html>not json"))
        self.assertEqual(result["state"], pd.PrinterState.CONNECTED)

    def test_server_error_is_only_found(self):
        result = pd.identify(self.entry(), opener=self.opener(500))
        self.assertEqual(result["state"], pd.PrinterState.FOUND)


class IdentifyByEvidenceTests(unittest.TestCase):
    """A port is a hint; the answer is the identity.

    Every case here reproduces something a real machine on a real network did:
    a QIDI X-Max 3 announced as a Prusa because port 80 was open, and a router
    listed as a printer because it was the first dialect asked.
    """

    MOONRAKER = (200, b'{"result": {"klipper_path": "/home/mks/klipper", '
                      b'"hostname": "mkspi", "state": "ready"}}')
    PRUSALINK = (200, b'{"api": "2.0.0", "text": "PrusaLink", '
                      b'"hostname": "prusa-core-one", "nozzle_diameter": 0.4}')
    ROUTER_HTML = (200, b"<?xml version=\"1.0\"?><html>router login</html>")

    def routed(self, table, default=(404, b"")):
        """Answer per URL, the way a host with several services really behaves."""
        def opener(request):
            for needle, response in table.items():
                if needle in request.full_url:
                    return response
            return default
        return opener

    def scanned(self, ports, address="192.168.0.71"):
        return {"kind": dict(pd.PROBE_PORTS)[ports[0]], "address": address,
                "port": ports[0], "ports": ports, "via": "scan",
                "label": address, "kind_verified": False}

    def test_klipper_box_with_port_80_open_is_not_called_a_prusa(self):
        # The QIDI X-Max 3: web UI on 80, Moonraker on 7125. The port guess says
        # prusalink; the answer says Klipper, and the answer wins.
        entry = self.scanned([80, 7125])
        self.assertEqual(entry["kind"], "prusalink")        # the wrong guess
        result = pd.identify(entry, opener=self.routed({":7125/printer/info": self.MOONRAKER}))
        self.assertEqual(result["kind"], "moonraker")
        self.assertTrue(result["kind_verified"])
        self.assertEqual(result["label"], "mkspi")

    def test_a_router_is_not_given_a_printer_type(self):
        entry = self.scanned([80], address="192.168.0.1")
        result = pd.identify(entry, opener=self.routed({"/api/version": self.ROUTER_HTML}))
        self.assertIsNone(result["kind"])
        self.assertEqual(result["state"], pd.PrinterState.FOUND)
        self.assertFalse(result["kind_verified"])

    def test_prusalink_is_recognised_by_its_own_answer(self):
        entry = self.scanned([80], address="192.168.0.170")
        result = pd.identify(entry, api_key="k",
                             opener=self.routed({"/api/version": self.PRUSALINK}))
        self.assertEqual(result["kind"], "prusalink")
        self.assertEqual(result["state"], pd.PrinterState.CONNECTED)
        self.assertEqual(result["nozzle_diameter"], 0.4)

    def test_locked_machine_keeps_the_guess_but_is_marked_unverified(self):
        # A printer behind a key is still probably a printer -- but nobody should
        # print to it on the strength of an open port.
        entry = self.scanned([80], address="10.0.0.4")
        result = pd.identify(entry, opener=self.routed({"/api/version": (401, b"")}))
        self.assertEqual(result["kind"], "prusalink")
        self.assertFalse(result["kind_verified"])
        self.assertEqual(result["state"], pd.PrinterState.RESPONDING)
        self.assertIn("unverified", result["note"])

    def test_mdns_service_type_is_trusted_without_re_deriving_it(self):
        # mDNS already answered the question: _prusalink._tcp IS the identity,
        # so it is confirmed rather than second-guessed against other dialects.
        entry = {"kind": "prusalink", "address": "192.168.0.170", "port": 80,
                 "via": "mdns", "label": "prusa-core-one"}
        asked = []

        def opener(request):
            asked.append(request.full_url)
            return self.PRUSALINK
        result = pd.identify(entry, api_key="k", opener=opener)
        self.assertEqual(result["kind"], "prusalink")
        self.assertEqual(len(asked), 1, "a trusted mDNS entry needs one question")

    def test_silence_everywhere_is_unreachable(self):
        def opener(request):
            raise OSError("no route")
        result = pd.identify(self.scanned([80, 7125]), opener=opener)
        self.assertEqual(result["state"], pd.PrinterState.UNREACHABLE)


class MergeWithConfigTests(unittest.TestCase):
    CONFIG = {
        "printers": {
            "core_one": {
                "label": "Prusa CORE One",
                "filaments": {"pla": {}},
                "network": {"type": "prusalink", "host": "192.168.0.170"},
            },
            "ston_wolf": {"label": "Ston Wolf", "network": {"type": "prusalink"}},
        }
    }

    def test_configured_and_seen_is_reported_with_its_state(self):
        rows = pd.merge_with_config(self.CONFIG, [
            {"address": "192.168.0.170", "state": pd.PrinterState.CONNECTED,
             "via": "mdns", "kind": "prusalink"}])
        row = [r for r in rows if r["key"] == "core_one"][0]
        self.assertEqual(row["state"], pd.PrinterState.CONNECTED)
        self.assertTrue(row["can_slice"])

    def test_configured_but_absent_is_unreachable(self):
        rows = pd.merge_with_config(self.CONFIG, [])
        row = [r for r in rows if r["key"] == "core_one"][0]
        self.assertEqual(row["state"], pd.PrinterState.UNREACHABLE)

    def test_printer_without_a_host_is_never_matched(self):
        rows = pd.merge_with_config(self.CONFIG, [
            {"address": "192.168.0.170", "state": pd.PrinterState.CONNECTED}])
        row = [r for r in rows if r["key"] == "ston_wolf"][0]
        self.assertEqual(row["state"], pd.PrinterState.UNREACHABLE)
        self.assertFalse(row["can_slice"])

    def test_discovered_stranger_is_listed_but_cannot_slice(self):
        # Found on the network is not the same as usable: without a slicer
        # profile there is nothing we could send it.
        rows = pd.merge_with_config(self.CONFIG, [
            {"address": "192.168.0.55", "state": pd.PrinterState.RESPONDING,
             "kind": "octoprint", "label": "octopi", "via": "mdns"}])
        stranger = [r for r in rows if r["address"] == "192.168.0.55"][0]
        self.assertFalse(stranger["configured"])
        self.assertFalse(stranger["can_slice"])
        self.assertIsNone(stranger["key"])

    def test_a_machine_is_not_listed_twice(self):
        rows = pd.merge_with_config(self.CONFIG, [
            {"address": "192.168.0.170", "state": pd.PrinterState.CONNECTED}])
        addresses = [r["address"] for r in rows if r["address"] == "192.168.0.170"]
        self.assertEqual(len(addresses), 1)

    def test_host_with_port_matches_the_bare_address(self):
        config = {"printers": {"qidi": {"label": "QIDI", "network": {
            "type": "moonraker", "host": "192.168.0.60:7125"}}}}
        rows = pd.merge_with_config(config, [
            {"address": "192.168.0.60", "state": pd.PrinterState.CONNECTED}])
        self.assertEqual(rows[0]["state"], pd.PrinterState.CONNECTED)

    def test_empty_config_still_lists_what_was_found(self):
        rows = pd.merge_with_config({}, [
            {"address": "10.0.0.2", "state": pd.PrinterState.FOUND, "kind": "prusalink"}])
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["configured"])


class ConfiguredEntriesTests(unittest.TestCase):
    """A printer whose address we know is asked, not waited for.

    Moonraker does not advertise itself by default, so a QIDI and a Ston WOLF
    both sat in the panel marked unreachable while answering every request sent
    to them. Discovery is for machines we cannot name; the config already named
    these.
    """

    CONFIG = {"printers": {
        "core_one": {"label": "Prusa CORE One",
                     "network": {"type": "prusalink", "host": "192.168.0.170"}},
        "qidi": {"label": "QIDI",
                 "network": {"type": "moonraker", "host": "192.168.0.71:7125"}},
        "blank": {"label": "Nenastavená", "network": {"type": "moonraker", "host": ""}},
        "none": {"label": "Bez sítě"},
    }}

    def test_every_configured_address_becomes_a_candidate(self):
        entries = pd.configured_entries(self.CONFIG)
        self.assertEqual({e["address"] for e in entries},
                         {"192.168.0.170", "192.168.0.71"})

    def test_port_is_taken_from_the_host_string(self):
        qidi = [e for e in pd.configured_entries(self.CONFIG)
                if e["address"] == "192.168.0.71"][0]
        self.assertEqual(qidi["port"], 7125)
        self.assertEqual(qidi["ports"], [7125])
        self.assertEqual(qidi["via"], "config")

    def test_printers_without_an_address_are_skipped(self):
        labels = {e["label"] for e in pd.configured_entries(self.CONFIG)}
        self.assertNotIn("Nenastavená", labels)
        self.assertNotIn("Bez sítě", labels)

    def test_a_malformed_port_does_not_raise(self):
        entries = pd.configured_entries({"printers": {"x": {
            "label": "x", "network": {"type": "moonraker", "host": "10.0.0.1:abc"}}}})
        self.assertEqual(entries[0]["port"], None)

    def test_config_entries_are_not_blindly_trusted_about_type(self):
        # via="config" is not via="mdns": the configured type is a starting guess
        # that still has to survive contact with the machine.
        entry = [e for e in pd.configured_entries(self.CONFIG)
                 if e["address"] == "192.168.0.170"][0]
        self.assertNotEqual(entry["via"], "mdns")

    def test_the_shipped_config_produces_usable_candidates(self):
        real = pd.load_config(os.path.join(helpers.ADDON_DIR, "slicer-config.json"))
        for entry in pd.configured_entries(real):
            self.assertTrue(entry["address"])
            self.assertIn(entry["kind"], ("prusalink", "moonraker", "octoprint"))


class BookPassthroughTests(unittest.TestCase):
    """What the book knows has to survive the merge.

    merge_with_config rebuilds every row from scratch, so anything it does not
    explicitly copy is lost. That is how a renamed printer kept showing its old
    config label and a manually added, switched-off machine vanished from the
    panel entirely -- both were in the book, neither survived the trip.
    """

    CONFIG = {"printers": {"core_one": {
        "label": "Prusa CORE One (0.4 nozzle, PLA loaded)", "filaments": {"pla": {}},
        "network": {"type": "prusalink", "host": "192.168.0.170"}}}}

    def test_user_name_outranks_the_config_label(self):
        rows = pd.merge_with_config(self.CONFIG, [{
            "address": "192.168.0.170", "state": pd.PrinterState.CONNECTED,
            "label": "Velká Prusa", "remembered": True, "book_id": "hostname:x"}])
        row = [r for r in rows if r["key"] == "core_one"][0]
        self.assertEqual(row["label"], "Velká Prusa")
        self.assertEqual(row["book_id"], "hostname:x")

    def test_remembered_machine_survives_even_when_unreachable(self):
        # A manually added printer that is switched off must stay in the list.
        rows = pd.merge_with_config({}, [{
            "address": "192.168.9.99", "state": pd.PrinterState.UNREACHABLE,
            "label": "Vypnutá u kamaráda", "remembered": True,
            "book_id": "addr:192.168.9.99", "kind": None}])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["remembered"])
        self.assertEqual(rows[0]["label"], "Vypnutá u kamaráda")

    def test_identity_mismatch_reaches_the_panel(self):
        rows = pd.merge_with_config(self.CONFIG, [{
            "address": "192.168.0.170", "state": pd.PrinterState.UNREACHABLE,
            "identity": "mismatch", "note": "something else is at this address",
            "remembered": True, "book_id": "hostname:x", "label": "Prusa"}])
        row = [r for r in rows if r["key"] == "core_one"][0]
        self.assertEqual(row["identity"], "mismatch")
        self.assertIn("something else", row["note"])

    def test_a_remembered_machine_is_not_told_it_is_unconfigured(self):
        rows = pd.merge_with_config({}, [{
            "address": "10.0.0.3", "state": pd.PrinterState.CONNECTED,
            "remembered": True, "book_id": "hostname:y", "label": "Moje",
            "kind": "moonraker"}])
        self.assertIsNone(rows[0].get("note"))

    def test_a_genuine_stranger_still_gets_the_hint(self):
        rows = pd.merge_with_config({}, [{
            "address": "10.0.0.4", "state": pd.PrinterState.FOUND, "kind": "prusalink"}])
        self.assertIn("not in the config", rows[0]["note"])


class SuggestedEntryTests(unittest.TestCase):
    def test_suggestion_carries_no_secret_and_no_guessed_profile(self):
        key, block = pd.suggest_config_entry({
            "kind": "prusalink", "address": "192.168.0.170", "port": 80,
            "label": "prusa-core-one"})
        self.assertEqual(key, "prusa_core_one")
        self.assertEqual(block["network"]["host"], "192.168.0.170")
        self.assertNotIn("api_key", block["network"])       # never inline a secret
        self.assertIn("api_key_file", block["network"])
        self.assertEqual(block["status"], "unconfigured")   # no profile invented

    def test_non_default_port_is_kept_in_the_host(self):
        _key, block = pd.suggest_config_entry({
            "kind": "moonraker", "address": "10.0.0.9", "port": 7125, "label": "qidi"})
        self.assertEqual(block["network"]["host"], "10.0.0.9:7125")

    def test_suggested_entry_is_json_serialisable(self):
        _key, block = pd.suggest_config_entry({"kind": "prusalink", "address": "1.2.3.4"})
        json.loads(json.dumps(block))


class BambuTests(unittest.TestCase):
    """Bambu announcements, parsed to the documented format.

    Honest caveat, repeated from the module: no Bambu was available to test
    against, so these exercise the parser on a synthetic packet in the shape the
    protocol documents. The failure direction is deliberately safe -- anything
    unrecognised yields None, so a wrong guess about the format means "no printer
    found", never a phantom machine in the list.
    """

    ANNOUNCEMENT = (
        "NOTIFY * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:2021\r\n"
        "Location: 192.168.0.55\r\n"
        "NT: urn:bambulab-com:device:3dprinter:1\r\n"
        "USN: 00M09A351100123\r\n"
        "DevModel.bambu.com: BL-P001\r\n"
        "DevName.bambu.com: Dílna X1C\r\n"
        "DevConnect.bambu.com: lan\r\n")

    def test_announcement_is_parsed(self):
        entry = pd.parse_bambu_announcement(self.ANNOUNCEMENT)
        self.assertEqual(entry["kind"], "bambu")
        self.assertEqual(entry["address"], "192.168.0.55")
        self.assertEqual(entry["serial"], "00M09A351100123")
        self.assertEqual(entry["label"], "Dílna X1C")
        self.assertEqual(entry["via"], "ssdp")

    def test_a_generic_ssdp_device_is_not_a_printer(self):
        # Every smart TV and router on the network speaks SSDP.
        other = ("NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                 "Location: http://192.168.0.9:8080/desc.xml\r\n"
                 "NT: urn:schemas-upnp-org:device:MediaRenderer:1\r\n")
        self.assertIsNone(pd.parse_bambu_announcement(other))

    def test_garbage_is_rejected(self):
        for junk in ("", "hello", "\x00\x01\x02", "NOTIFY"):
            self.assertIsNone(pd.parse_bambu_announcement(junk))

    def test_announcement_without_an_address_is_useless(self):
        without = self.ANNOUNCEMENT.replace("Location: 192.168.0.55\r\n", "")
        self.assertIsNone(pd.parse_bambu_announcement(without))

    def test_bambu_is_not_probed_over_http(self):
        # Its LAN interface is MQTT/FTPS; pretending to have asked an HTTP API
        # would be inventing evidence.
        asked = []

        def opener(request):
            asked.append(request.full_url)
            return (200, b"{}")
        entry = pd.parse_bambu_announcement(self.ANNOUNCEMENT)
        result = pd.identify(entry, opener=opener)
        self.assertEqual(asked, [])
        self.assertEqual(result["kind"], "bambu")
        self.assertEqual(result["state"], pd.PrinterState.FOUND)

    def test_a_discovered_bambu_cannot_be_sliced_for(self):
        entry = pd.parse_bambu_announcement(self.ANNOUNCEMENT)
        entry["state"] = pd.PrinterState.FOUND
        rows = pd.merge_with_config({}, [entry])
        self.assertFalse(rows[0]["can_slice"])


class AddToConfigTests(unittest.TestCase):
    """Writing a discovered machine into slicer-config.json."""

    def setUp(self):
        import shutil
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "slicer-config.json")
        shutil.copy(os.path.join(helpers.ADDON_DIR, "slicer-config.json"), self.path)

    def load(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_entry_is_added_with_the_network_block(self):
        key, block = pd.add_to_config(
            {"kind": "moonraker", "address": "10.0.0.9", "port": 7125,
             "label": "nová"}, path=self.path)
        stored = self.load()["printers"][key]
        self.assertEqual(stored["network"]["host"], "10.0.0.9:7125")
        self.assertEqual(block["status"], "unconfigured")

    def test_no_slicer_profile_is_invented(self):
        # A guessed profile would slice real G-code for a machine that never had
        # it -- the printer must stay unsliceable until a human fills this in.
        key, _ = pd.add_to_config({"kind": "prusalink", "address": "10.0.0.8",
                                   "label": "x"}, path=self.path)
        stored = self.load()["printers"][key]
        for field in ("printer_profile", "profile", "filaments"):
            self.assertNotIn(field, stored)
        rows = pd.merge_with_config(self.load(), [])
        self.assertFalse([r for r in rows if r["key"] == key][0]["can_slice"])

    def test_existing_printers_are_untouched(self):
        before = self.load()["printers"]["core_one"]
        pd.add_to_config({"kind": "prusalink", "address": "10.0.0.8", "label": "x"},
                         path=self.path)
        self.assertEqual(self.load()["printers"]["core_one"], before)

    def test_a_name_clash_does_not_overwrite(self):
        row = {"kind": "prusalink", "address": "10.0.0.8", "label": "core one"}
        first, _ = pd.add_to_config(row, path=self.path)
        second, _ = pd.add_to_config(row, path=self.path)
        self.assertNotEqual(first, second)
        self.assertIn(first, self.load()["printers"])

    def test_the_users_label_is_kept(self):
        key, _ = pd.add_to_config({"kind": "prusalink", "address": "10.0.0.8"},
                                  path=self.path, label="QIDI v dílně")
        self.assertEqual(self.load()["printers"][key]["label"], "QIDI v dílně")

    def test_no_secret_is_written_into_the_tracked_file(self):
        key, _ = pd.add_to_config({"kind": "prusalink", "address": "10.0.0.8",
                                   "label": "x"}, path=self.path)
        network = self.load()["printers"][key]["network"]
        self.assertNotIn("api_key", network)
        self.assertIn("api_key_file", network)

    def test_a_broken_config_is_reported_not_overwritten(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ broken")
        with self.assertRaises(ValueError):
            pd.add_to_config({"kind": "prusalink", "address": "1.2.3.4"},
                             path=self.path)


class SubnetTests(unittest.TestCase):
    def test_subnet_covers_usable_addresses_only(self):
        hosts = pd.subnet_hosts("192.168.0.170")
        self.assertEqual(len(hosts), 254)
        self.assertNotIn("192.168.0.0", hosts)
        self.assertNotIn("192.168.0.255", hosts)

    def test_non_24_prefix_is_refused_rather_than_swept(self):
        # Sweeping a /16 would be 65k connections; declining is the right answer.
        self.assertEqual(pd.subnet_hosts("10.0.0.1", prefix=16), [])

    def test_scan_skips_our_own_address(self):
        knocked = []

        def probe(host, port, timeout):
            knocked.append(host)
            return False
        pd.scan_subnet("192.168.0.170", _probe=probe)
        self.assertNotIn("192.168.0.170", knocked)

    def test_scan_keeps_every_open_port(self):
        # Stopping at the first open port is what hid the QIDI's Moonraker API
        # behind its web UI and got it identified as the wrong make of printer.
        def probe(host, port, timeout):
            return host == "192.168.0.5" and port in (80, 7125)
        found = pd.scan_subnet("192.168.0.1", _probe=probe)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["ports"], [80, 7125])
        self.assertEqual(found[0]["via"], "scan")
        self.assertFalse(found[0]["kind_verified"])

    def test_cancelling_stops_knocking(self):
        """Cancel must stop work, not merely stop reading results.

        The guarantee is bounded by one batch -- addresses already handed to the
        pool finish -- so with a width of four, cancelling after the first batch
        must leave the other 250 addresses untouched.
        """
        knocked = []

        def probe(host, port, timeout):
            knocked.append(host)
            return False
        pd.scan_subnet("192.168.0.1", progress=lambda done, total: False,
                       _probe=probe, workers=4)
        self.assertLessEqual(len(set(knocked)), 4)

    def test_a_full_sweep_visits_every_address(self):
        knocked = set()

        def probe(host, port, timeout):
            knocked.add(host)
            return False
        pd.scan_subnet("192.168.0.1", _probe=probe, workers=8)
        self.assertEqual(len(knocked), 253)      # 254 minus our own address


if __name__ == "__main__":
    unittest.main()
