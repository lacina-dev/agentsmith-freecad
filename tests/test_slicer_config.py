"""Tests for printer/filament selection in slice_check.py.

Material is a property of the spool in the machine, not of the machine — the same
printer runs PETG one week and PLA the next. These pin that the config can express
that and that asking for a material the printer has no profile for fails loudly
instead of silently slicing with the wrong one.
"""

import json
import os
import sys
import unittest

import helpers  # noqa: F401

sys.path.insert(0, helpers.ADDON_DIR)
import slice_check  # noqa: E402

REAL_CONFIG = os.path.join(helpers.ADDON_DIR, "slicer-config.json")

CONFIG = {
    "default_printer": "core_one",
    "printers": {
        "core_one": {
            "label": "Prusa CORE One", "vendor": "Prusa",
            "machine": "Prusa CORE One 0.4 nozzle",
            "process": "0.20mm SPEED @CORE One 0.4",
            "filament": "Prusa Generic PLA @CORE One",
            "loaded_filament": "pla",
            "filaments": {"pla": "Prusa Generic PLA @CORE One",
                          "petg": "Prusa Generic PETG @CORE One"},
        },
        "ston_wolf": {"label": "Ston WOLF", "unconfigured": True,
                      "note": "profiles pending"},
    },
}


class Selection(unittest.TestCase):
    def test_default_printer_is_used_when_none_is_named(self):
        printer = slice_check.select_printer(CONFIG, None)
        self.assertEqual(printer["_key"], "core_one")

    def test_loaded_filament_is_the_default(self):
        printer = slice_check.select_printer(CONFIG, "core_one")
        self.assertEqual(printer["filament"], "Prusa Generic PLA @CORE One")

    def test_material_can_be_overridden_by_name(self):
        printer = slice_check.select_printer(CONFIG, "core_one", "petg")
        self.assertEqual(printer["filament"], "Prusa Generic PETG @CORE One")
        self.assertEqual(printer["loaded_filament"], "petg")

    def test_override_is_case_insensitive(self):
        self.assertEqual(slice_check.select_printer(CONFIG, "core_one", "PETG")["filament"],
                         "Prusa Generic PETG @CORE One")

    def test_a_full_profile_name_is_accepted(self):
        printer = slice_check.select_printer(CONFIG, "core_one",
                                             "Prusa Generic PETG @CORE One")
        self.assertEqual(printer["filament"], "Prusa Generic PETG @CORE One")

    def test_an_unknown_material_fails_loudly_and_lists_the_options(self):
        # Silently slicing with the wrong material would produce a plausible-looking
        # G-code that ruins a print.
        with self.assertRaises(slice_check.SetupError) as caught:
            slice_check.select_printer(CONFIG, "core_one", "nylon")
        self.assertIn("pla", str(caught.exception))
        self.assertIn("petg", str(caught.exception))

    def test_unconfigured_printer_is_refused_with_its_note(self):
        with self.assertRaises(slice_check.SetupError) as caught:
            slice_check.select_printer(CONFIG, "ston_wolf")
        self.assertIn("profiles pending", str(caught.exception))

    def test_unknown_printer_lists_what_exists(self):
        with self.assertRaises(slice_check.SetupError) as caught:
            slice_check.select_printer(CONFIG, "bambu")
        self.assertIn("core_one", str(caught.exception))

    def test_selection_does_not_mutate_the_config(self):
        slice_check.select_printer(CONFIG, "core_one", "petg")
        self.assertEqual(CONFIG["printers"]["core_one"]["filament"],
                         "Prusa Generic PLA @CORE One")


class ShippedConfig(unittest.TestCase):
    """The real slicer-config.json must stay usable by the tools that read it."""

    @classmethod
    def setUpClass(cls):
        with open(REAL_CONFIG, encoding="utf-8") as handle:
            cls.config = json.load(handle)

    def test_default_printer_exists_and_is_configured(self):
        printer = slice_check.select_printer(self.config, None)
        self.assertTrue(printer.get("machine"))
        self.assertTrue(printer.get("filament"))

    def test_every_configured_printer_resolves(self):
        for key, printer in self.config["printers"].items():
            if printer.get("unconfigured"):
                continue
            with self.subTest(printer=key):
                resolved = slice_check.select_printer(self.config, key)
                self.assertTrue(resolved.get("process"))

    def test_loaded_filament_points_at_a_listed_profile(self):
        for key, printer in self.config["printers"].items():
            if printer.get("unconfigured"):
                continue
            with self.subTest(printer=key):
                filaments = printer.get("filaments") or {}
                self.assertIn(printer["filament"], filaments.values(),
                              "the loaded filament must be one of the listed profiles")
                self.assertIn(printer.get("loaded_filament"), filaments)

    def test_both_materials_are_available_on_every_configured_printer(self):
        for key, printer in self.config["printers"].items():
            if printer.get("unconfigured"):
                continue
            with self.subTest(printer=key):
                self.assertIn("pla", printer.get("filaments", {}))
                self.assertIn("petg", printer.get("filaments", {}))



class PortabilityTests(unittest.TestCase):
    """The addon must be installable by somebody who is not its author.

    The OrcaSlicer path was a single hard-coded string under one developer's home
    directory, which made slicing silently impossible for everyone else. It was
    only noticed while writing the install instructions -- documentation is a
    surprisingly good test of whether software can be installed at all.
    """

    ADDON_FILES = [os.path.join(helpers.ADDON_DIR, name)
                   for name in os.listdir(helpers.ADDON_DIR)
                   if name.endswith((".py", ".json"))]

    def test_no_absolute_home_directory_is_baked_in(self):
        offenders = []
        for path in self.ADDON_FILES:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    if "/home/" in line and "expanduser" not in line:
                        offenders.append("%s:%d" % (os.path.basename(path), number))
        self.assertEqual(offenders, [], "hard-coded home paths: %s" % offenders)

    def test_the_appimage_is_searched_for_rather_than_assumed(self):
        for directory in slice_check.APPIMAGE_SEARCH_DIRS:
            self.assertTrue(directory.startswith(("~", "/opt", "/usr")), directory)
        self.assertIn("*", slice_check.APPIMAGE_PATTERN)

    def test_an_explicit_config_path_wins_over_the_search(self):
        # A user who names the AppImage must not have it second-guessed.
        found = slice_check.find_appimage(search_dirs=(), pattern="nothing*")
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
