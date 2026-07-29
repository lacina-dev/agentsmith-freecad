"""Tests for eval/host_bridge.py — the launch-or-reuse decision.

This tool exists because its ad-hoc predecessor launched a second FreeCAD when
the first one was merely slow to boot. Two GUI instances then fought over one
discovery file until the user saw phantom windows. Every test here pins a piece
of the "never double-launch" contract without touching FreeCAD or the network.
"""

import json
import os
import sys
import tempfile
import unittest

import helpers  # noqa: F401

sys.path.insert(0, os.path.join(helpers.REPO_ROOT, "eval"))
import host_bridge  # noqa: E402


class BridgeIsLive(unittest.TestCase):
    """Live requires BOTH a living pid and an answering socket."""

    def test_no_discovery_is_not_live(self):
        self.assertFalse(host_bridge.bridge_is_live(
            None, ping_fn=lambda d: True, pid_alive_fn=lambda p: True))

    def test_dead_pid_is_not_live_even_if_socket_answers(self):
        # A lingering socket on a reused port belongs to someone else.
        self.assertFalse(host_bridge.bridge_is_live(
            {"pid": 1234, "port": 1}, ping_fn=lambda d: True,
            pid_alive_fn=lambda p: False))

    def test_live_pid_with_dead_socket_is_not_live(self):
        # FreeCAD running with its bridge stopped must not count as reusable.
        self.assertFalse(host_bridge.bridge_is_live(
            {"pid": 1234, "port": 1}, ping_fn=lambda d: False,
            pid_alive_fn=lambda p: True))

    def test_both_alive_is_live(self):
        self.assertTrue(host_bridge.bridge_is_live(
            {"pid": 1234, "port": 1}, ping_fn=lambda d: True,
            pid_alive_fn=lambda p: True))


class ReadDiscovery(unittest.TestCase):
    def test_missing_file_returns_none(self):
        self.assertIsNone(host_bridge.read_discovery("/nonexistent/discovery.json"))

    def test_corrupt_file_returns_none(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{not json")
            path = handle.name
        try:
            self.assertIsNone(host_bridge.read_discovery(path))
        finally:
            os.unlink(path)

    def test_valid_file_round_trips(self):
        record = {"pid": 42, "port": 18421, "token": "t"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(record, handle)
            path = handle.name
        try:
            self.assertEqual(host_bridge.read_discovery(path), record)
        finally:
            os.unlink(path)


class AcquireLock(unittest.TestCase):
    """Two concurrent launchers must collapse to one launch."""

    def setUp(self):
        self.lock = os.path.join(tempfile.mkdtemp(), "host.lock")

    def test_free_lock_is_acquired(self):
        self.assertTrue(host_bridge.acquire_lock(self.lock))
        with open(self.lock) as handle:
            self.assertEqual(int(handle.read()), os.getpid())

    def test_held_lock_with_living_holder_is_refused(self):
        with open(self.lock, "w") as handle:
            handle.write("99999")
        self.assertFalse(host_bridge.acquire_lock(
            self.lock, pid_alive_fn=lambda p: True))

    def test_stale_lock_with_dead_holder_is_taken_over(self):
        # A launcher that died between launch and cleanup must not block
        # every future launcher forever.
        with open(self.lock, "w") as handle:
            handle.write("99999")
        self.assertTrue(host_bridge.acquire_lock(
            self.lock, pid_alive_fn=lambda p: False))

    def test_corrupt_lock_counts_as_stale(self):
        with open(self.lock, "w") as handle:
            handle.write("not-a-pid")
        self.assertTrue(host_bridge.acquire_lock(
            self.lock, pid_alive_fn=lambda p: p != 0 and False))


class FindAppimage(unittest.TestCase):
    def test_env_override_wins_when_it_exists(self):
        with tempfile.NamedTemporaryFile(suffix=".AppImage") as handle:
            found = host_bridge.find_appimage({host_bridge.APPIMAGE_ENV: handle.name})
            self.assertEqual(found, handle.name)

    def test_env_override_pointing_nowhere_fails_loudly(self):
        # A typo in the override must not silently fall through to a different
        # FreeCAD than the one the user named.
        with self.assertRaises(SystemExit):
            host_bridge.find_appimage({host_bridge.APPIMAGE_ENV: "/nonexistent/fc.AppImage"})


if __name__ == "__main__":
    unittest.main()
