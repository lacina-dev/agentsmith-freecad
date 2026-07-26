"""Tests for the remembered printer list.

The two rules this module exists to keep:

A remembered printer never disappears. Carrying a laptop from home to the office
must leave the list intact -- the printers are still owned, merely out of reach.

An address is not an identity. The same 192.168.0.x is a printer in one building
and a stranger's NAS in another, so a remembered entry is only claimed as present
when the machine answering still matches the fingerprint we stored. Showing
somebody else's device as the user's printer -- with a Start button next to it --
is the failure these tests exist to prevent.
"""

import json
import os
import sys
import tempfile
import time
import unittest

import helpers  # noqa: F401

sys.path.insert(0, helpers.ADDON_DIR)
import printer_book as book  # noqa: E402

CORE_ONE = {"address": "192.168.0.170", "port": 80, "kind": "prusalink",
            "hostname": "prusa-core-one.local", "text": "PrusaLink",
            "label": "prusa-core-one"}
QIDI = {"address": "192.168.0.71", "port": 7125, "kind": "moonraker",
        "hostname": "mkspi", "label": "mkspi"}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "nested", "printers.json")

    def test_round_trip(self):
        data = {"version": 1, "printers": [{"id": "x", "name": "Tiskárna"}]}
        book.save_book(data, self.path)
        self.assertEqual(book.load_book(self.path)["printers"][0]["name"], "Tiskárna")

    def test_missing_file_is_an_empty_book(self):
        self.assertEqual(book.load_book(os.path.join(self.dir, "nope.json"))["printers"], [])

    def test_corrupt_file_does_not_raise(self):
        # A broken book must not stop the panel from opening.
        with open(self.path.replace("nested/", ""), "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        self.assertEqual(book.load_book(self.path.replace("nested/", ""))["printers"], [])

    def test_wrong_shape_is_rejected(self):
        path = os.path.join(self.dir, "list.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(["not", "a", "book"], handle)
        self.assertEqual(book.load_book(path)["printers"], [])

    def test_save_is_atomic(self):
        book.save_book({"version": 1, "printers": []}, self.path)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_no_secret_is_written(self):
        data = {"version": 1, "printers": []}
        book.add_manual(data, "10.0.0.1", name="X")
        book.save_book(data, self.path)
        with open(self.path, encoding="utf-8") as handle:
            text = handle.read().lower()
        for forbidden in ("api_key", "password", "x-api-key"):
            self.assertNotIn(forbidden, text)


class IdentityTests(unittest.TestCase):
    def test_hostname_beats_address_as_the_key(self):
        # DHCP moves printers; the identity must not move with the lease.
        moved = dict(CORE_ONE, address="192.168.1.55")
        self.assertEqual(book.entry_id(CORE_ONE), book.entry_id(moved))

    def test_address_is_only_the_last_resort(self):
        anonymous = {"address": "10.0.0.9"}
        self.assertTrue(book.entry_id(anonymous).startswith("addr:"))

    def test_firmware_version_is_not_part_of_the_fingerprint(self):
        # A printer that updated itself is still the same printer.
        fingerprint = book.fingerprint_of(dict(CORE_ONE, firmware="6.5.3"))
        self.assertNotIn("firmware", fingerprint)

    def test_same_machine_matches(self):
        remembered = {"fingerprint": book.fingerprint_of(CORE_ONE)}
        verdict, _ = book.identity_matches(remembered, CORE_ONE)
        self.assertEqual(verdict, "match")

    def test_a_stranger_at_the_same_address_is_a_mismatch(self):
        remembered = {"fingerprint": book.fingerprint_of(CORE_ONE)}
        stranger = {"address": "192.168.0.170", "hostname": "office-nas",
                    "text": "Synology"}
        verdict, reason = book.identity_matches(remembered, stranger)
        self.assertEqual(verdict, "mismatch")
        self.assertIn("office-nas", reason)

    def test_never_fingerprinted_is_unknown_not_yes(self):
        verdict, _ = book.identity_matches({}, CORE_ONE)
        self.assertEqual(verdict, "unknown")

    def test_silent_machine_is_unknown_not_yes(self):
        remembered = {"fingerprint": book.fingerprint_of(CORE_ONE)}
        verdict, _ = book.identity_matches(remembered, {"address": "192.168.0.170"})
        self.assertEqual(verdict, "unknown")

    def test_comparison_ignores_case(self):
        remembered = {"fingerprint": book.fingerprint_of(CORE_ONE)}
        shouty = dict(CORE_ONE, hostname="PRUSA-CORE-ONE.LOCAL", text="PRUSALINK")
        verdict, _ = book.identity_matches(remembered, shouty)
        self.assertEqual(verdict, "match")


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.data = {"version": 1, "printers": []}

    def test_manual_add_works_without_the_printer_answering(self):
        # A switched-off printer must still be addable.
        record = book.add_manual(self.data, "192.168.0.99", name="Dílna")
        self.assertEqual(record["name"], "Dílna")
        self.assertEqual(record["source"], "manual")
        self.assertEqual(len(self.data["printers"]), 1)

    def test_blank_address_is_refused(self):
        with self.assertRaises(ValueError):
            book.add_manual(self.data, "   ")

    def test_rediscovery_does_not_overwrite_the_users_name(self):
        book.remember(self.data, CORE_ONE, name="Velká Prusa")
        book.remember(self.data, CORE_ONE)                 # seen again
        self.assertEqual(self.data["printers"][0]["name"], "Velká Prusa")
        self.assertEqual(len(self.data["printers"]), 1)

    def test_rediscovery_updates_the_address(self):
        book.remember(self.data, CORE_ONE, name="Prusa")
        book.remember(self.data, dict(CORE_ONE, address="192.168.5.5"))
        self.assertEqual(self.data["printers"][0]["address"], "192.168.5.5")
        self.assertEqual(len(self.data["printers"]), 1)

    def test_manual_entry_is_upgraded_once_the_printer_identifies_itself(self):
        book.add_manual(self.data, "192.168.0.170", name="Dílna")
        book.remember(self.data, CORE_ONE)
        self.assertEqual(len(self.data["printers"]), 1)
        record = self.data["printers"][0]
        self.assertEqual(record["name"], "Dílna")          # the user's name survives
        self.assertIn("fingerprint", record)
        self.assertFalse(record["id"].startswith("addr:"))

    def test_rename(self):
        record = book.remember(self.data, CORE_ONE)
        book.rename(self.data, record["id"], "  U okna  ")
        self.assertEqual(self.data["printers"][0]["name"], "U okna")

    def test_rename_to_blank_is_ignored(self):
        record = book.remember(self.data, CORE_ONE, name="Prusa")
        book.rename(self.data, record["id"], "   ")
        self.assertEqual(self.data["printers"][0]["name"], "Prusa")

    def test_rename_unknown_id_is_harmless(self):
        self.assertIsNone(book.rename(self.data, "nope", "X"))

    def test_forget(self):
        record = book.remember(self.data, CORE_ONE)
        self.assertTrue(book.forget(self.data, record["id"]))
        self.assertEqual(self.data["printers"], [])
        self.assertFalse(book.forget(self.data, record["id"]))

    def test_two_printers_are_kept_apart(self):
        book.remember(self.data, CORE_ONE)
        book.remember(self.data, QIDI)
        self.assertEqual(len(self.data["printers"]), 2)


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.data = {"version": 1, "printers": []}
        book.remember(self.data, CORE_ONE, name="Prusa doma", now="2026-07-20T10:00:00")
        book.remember(self.data, QIDI, name="QIDI v dílně", now="2026-07-20T10:00:00")

    def test_book_entries_are_probe_candidates(self):
        entries = book.book_entries(self.data)
        self.assertEqual({e["address"] for e in entries},
                         {"192.168.0.170", "192.168.0.71"})
        self.assertTrue(all(e["via"] == "book" for e in entries))

    def test_printers_that_did_not_answer_are_still_listed(self):
        # Moving to another network must not empty the list.
        rows = book.unseen_rows(self.data, seen_ids=set())
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["state"] == "unreachable" for r in rows))
        self.assertIn("2026-07-20", rows[0]["note"])

    def test_answered_printers_are_not_listed_twice(self):
        seen = {p["id"] for p in self.data["printers"][:1]}
        self.assertEqual(len(book.unseen_rows(self.data, seen)), 1)

    def test_the_remembered_list_is_displayable_before_any_network_call(self):
        """The panel opens with the printers already in it.

        Opening empty and waiting for a button press throws away the point of
        remembering them: the machines are known, only their current state is
        not. These rows come straight off disk and say "checking" rather than
        claiming a state nobody has verified yet.
        """
        rows = book.cached_rows(self.data)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["state"] for r in rows}, {"checking"})
        self.assertEqual({r["label"] for r in rows}, {"Prusa doma", "QIDI v dílně"})
        self.assertTrue(all(r["remembered"] for r in rows))
        self.assertTrue(all(r["book_id"] for r in rows))

    def test_cached_rows_never_claim_a_printer_is_usable(self):
        # Nothing has been checked yet, so nothing may look ready.
        for row in book.cached_rows(self.data):
            self.assertFalse(row["can_slice"])
            self.assertNotEqual(row["state"], "connected")

    def test_an_empty_book_shows_nothing_rather_than_failing(self):
        self.assertEqual(book.cached_rows({"printers": []}), [])
        self.assertEqual(book.cached_rows({}), [])

    def test_monitored_state_is_recorded(self):
        printer = self.data["printers"][0]
        book.record_state(self.data, printer["id"], "connected", can_slice=True,
                          now="2026-07-25T18:00:00")
        self.assertEqual(printer["last_state"], "connected")
        self.assertTrue(printer["can_slice"])
        self.assertEqual(printer["last_seen"], "2026-07-25T18:00:00")

    def test_being_unreachable_does_not_refresh_last_seen(self):
        # "Last seen" means seen, not looked for.
        printer = self.data["printers"][0]
        before = printer["last_seen"]
        book.record_state(self.data, printer["id"], "unreachable",
                          now="2026-07-25T18:00:00")
        self.assertEqual(printer["last_seen"], before)
        self.assertEqual(printer["last_state"], "unreachable")

    def test_recording_an_unknown_printer_is_harmless(self):
        self.assertIsNone(book.record_state(self.data, "nope", "connected"))


class StatusSummaryTests(unittest.TestCase):
    """What the agent is told about the fleet.

    The point of monitoring is that "print it on the Prusa" does not begin with
    the agent rediscovering three printers -- and, more importantly, that a
    machine which is switched off is known to be switched off before a task
    plans around it. Which is exactly why a stale reading must not be quoted: a
    printer reported ready on the strength of an hour-old check is worse than no
    information, because it reads as verified.
    """

    def setUp(self):
        self.data = {"version": 1, "printers": []}
        record = book.remember(self.data, CORE_ONE, name="Prusa doma")
        book.record_state(self.data, record["id"], "connected", can_slice=True,
                          now="2026-07-25T18:00:00")
        self.now = time.mktime(time.strptime("2026-07-25T18:00:30",
                                             "%Y-%m-%dT%H:%M:%S"))

    def test_a_fresh_reading_is_quoted(self):
        summary = book.status_summary(self.data, now=self.now)
        self.assertIn("Prusa doma", summary)
        self.assertIn("připojena", summary)
        self.assertIn("slicovat umím", summary)

    def test_a_stale_reading_becomes_unknown(self):
        later = self.now + book.STATE_FRESHNESS_SECONDS + 60
        summary = book.status_summary(self.data, now=later)
        self.assertIn("stav neznámý", summary)
        self.assertNotIn("připojena", summary)

    def test_a_printer_never_checked_is_unknown_not_absent(self):
        book.add_manual(self.data, "10.0.0.9", name="Nová")
        summary = book.status_summary(self.data, now=self.now)
        self.assertIn("Nová", summary)
        self.assertIn("stav neznámý", summary)

    def test_missing_slicer_profile_is_stated_plainly(self):
        record = book.remember(self.data, QIDI, name="QIDI")
        book.record_state(self.data, record["id"], "connected", can_slice=False,
                          now="2026-07-25T18:00:00")
        summary = book.status_summary(self.data, now=self.now)
        self.assertIn("neumím (chybí profil)", summary)

    def test_a_corrupt_timestamp_is_treated_as_unknown(self):
        self.data["printers"][0]["last_state_at"] = "kdysi"
        self.assertIn("stav neznámý", book.status_summary(self.data, now=self.now))

    def test_an_empty_book_produces_no_context(self):
        self.assertEqual(book.status_summary({"printers": []}), "")


class AnnotateTests(unittest.TestCase):
    def setUp(self):
        self.data = {"version": 1, "printers": []}
        book.remember(self.data, CORE_ONE, name="Prusa doma", now="2026-07-20T10:00:00")
        book.remember(self.data, QIDI, name="QIDI v dílně", now="2026-07-20T10:00:00")

    def test_annotate_applies_the_user_name(self):
        identified = [dict(CORE_ONE, state="connected",
                           book_id=self.data["printers"][0]["id"])]
        row = book.annotate(self.data, identified)[0]
        self.assertEqual(row["label"], "Prusa doma")
        self.assertEqual(row["identity"], "match")
        self.assertEqual(row["state"], "connected")

    def test_a_stranger_at_a_remembered_address_is_not_claimed(self):
        # The whole point: something answers, but it is not our printer, so it
        # must not be presented as connected with a Start button beside it.
        stranger = {"address": "192.168.0.170", "hostname": "office-nas",
                    "text": "Synology", "state": "connected",
                    "book_id": self.data["printers"][0]["id"]}
        row = book.annotate(self.data, [stranger])[0]
        self.assertEqual(row["identity"], "mismatch")
        self.assertEqual(row["state"], "unreachable")
        self.assertIn("office-nas", row["note"])

    def test_unknown_machines_pass_through_untouched(self):
        row = book.annotate(self.data, [{"address": "10.9.9.9", "state": "found"}])[0]
        self.assertNotIn("remembered", row)


if __name__ == "__main__":
    unittest.main()
