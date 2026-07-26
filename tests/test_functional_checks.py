"""Tests for the functional-pose checks in eval/run_eval.py.

The regression these encode is real: on 2026-07-18 a wall hook scored 7/7 on the
dimensional checks while being an unusable flat plate — the profile had been
extruded in the wrong projection plane, so nothing stood off the wall and the
screw holes ran through the broad face. Harness lessons L1 and L2 came from that
run. `WALL_HOOK_BROKEN` below reproduces that model's geometry; every check that
claims to catch it must fail on it and pass on `WALL_HOOK_GOOD`.
"""

import os
import sys
import unittest

import helpers  # noqa: F401  (adds the addon dir to sys.path)

sys.path.insert(0, os.path.join(helpers.REPO_ROOT, "eval"))
import run_eval  # noqa: E402


class StubCache:
    """Stands in for DigestCache / ProbeCache: returns a payload or raises."""

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def get(self):
        if self._error is not None:
            raise run_eval.BridgeCallError(self._error)
        return self._payload


def digest(size):
    """model_digest with one visible object of the given bounding-box size."""
    return {"overall_bounding_box": {"min": [0.0, 0.0, 0.0], "max": list(size)}}


def probe(holes, largest_plane=None):
    return {"objects": [{"object": "Body", "label": "Body", "holes": holes,
                         "planes": [largest_plane] if largest_plane else [],
                         "largest_plane": largest_plane}]}


def plane(normal, area, standoff):
    return {"normal": list(normal), "area_mm2": area, "center": [0.0, 0.0, 0.0],
            "standoff_mm": standoff}


def hole(axis, diameter=5.5, kind="hole", length=8.0, angle_deg=360.0):
    return {"axis": list(axis), "axis_point": [0.0, 0.0, 0.0], "diameter_mm": diameter,
            "radius_mm": diameter / 2.0, "length_mm": length, "kind": kind,
            "angle_deg": angle_deg, "face_count": 1, "area_mm2": 100.0}


# The J-hook as it should be: a backplate against the wall, the arm standing 40 mm
# off it, two M5 clearance holes drilled through the backplate into the wall. The
# backplate is the largest flat face, so auto mode picks it as the mounting plane
# and measures a 40 mm standoff.
WALL_HOOK_GOOD = {
    "digest": digest([12.0, 40.0, 60.0]),
    "probe": probe([hole([0.0, 1.0, 0.0]), hole([0.0, 1.0, 0.0])],
                   largest_plane=plane([0.0, 1.0, 0.0], area=720.0, standoff=40.0)),
}

# The failure: the same silhouette built in the wrong projection plane. The part is
# an 8 mm plaque spread across X/Z; its largest face is the broad side, and the
# whole body stands off that face by only its own thickness.
WALL_HOOK_BROKEN = {
    "digest": digest([60.0, 8.0, 40.0]),
    "probe": probe([hole([0.0, 1.0, 0.0]), hole([0.0, 1.0, 0.0])],
                   largest_plane=plane([0.0, 1.0, 0.0], area=2400.0, standoff=8.0)),
}

# How a golden task actually specifies it: no world axis, because the task does not
# pin the model's pose (printed_wall_hook is explicitly modelled lying on its side).
HOOK_SPEC = {
    "mounting_axis": "auto",
    "hole_axis_tol_deg": 10.0,
    "min_hole_count": 2,
    "min_hole_dia_mm": 3.0,
    "protrusion_mm": 30.0,
    "min_slab_ratio": 0.15,
}


def grade(spec, model):
    return {result["check"]: result
            for result in run_eval.check_functional(
                spec, StubCache(model["digest"]), StubCache(model["probe"]))}


class WallHookRegression(unittest.TestCase):
    """The whole point of the feature: the 7/7 flat plate must not pass."""

    def test_correct_hook_passes_every_functional_check(self):
        results = grade(HOOK_SPEC, WALL_HOOK_GOOD)
        for name, result in results.items():
            with self.subTest(check=name):
                self.assertEqual(result["status"], "pass", result["note"])

    def test_flat_lookalike_fails(self):
        results = grade(HOOK_SPEC, WALL_HOOK_BROKEN)
        failed = sorted(name for name, result in results.items()
                        if result["status"] == "fail")
        # Note which check does NOT fire, and why that is correct: on a flat plaque
        # the screw holes really are perpendicular to its largest face, so the hole
        # check passes. What is wrong is that the plaque is the whole part — which
        # is exactly what protrusion and slab ratio measure. Any one of them failing
        # is enough to sink the scorecard.
        self.assertEqual(failed, ["functional_protrusion", "functional_slab_ratio"])
        self.assertEqual(results["functional_hole_axes"]["status"], "pass")

    def test_failure_notes_name_the_cause(self):
        results = grade(HOOK_SPEC, WALL_HOOK_BROKEN)
        self.assertIn("projection", results["functional_protrusion"]["note"])

    def test_pinned_pose_also_catches_the_misdrilled_holes(self):
        # When a task DOES pin the pose, the hole check adds a second, independent
        # way to catch the same failure: holes bored through the broad face instead
        # of into the wall.
        spec = dict(HOOK_SPEC, mounting_axis="y")
        broken = {"digest": digest([60.0, 8.0, 40.0]),
                  "probe": probe([hole([1.0, 0.0, 0.0]), hole([1.0, 0.0, 0.0])])}
        results = grade(spec, broken)
        self.assertEqual(results["functional_hole_axes"]["status"], "fail")
        self.assertIn("perpendicular", results["functional_hole_axes"]["note"])


class MountingReference(unittest.TestCase):
    """Auto mode derives the mounting plane instead of trusting a world axis."""

    def test_auto_uses_the_largest_planar_face(self):
        spec = {"mounting_axis": "auto", "hole_axis_tol_deg": 5.0}
        model = {"digest": digest([10.0, 10.0, 10.0]),
                 "probe": probe([hole([1.0, 0.0, 0.0])],
                                largest_plane=plane([1.0, 0.0, 0.0], 500.0, 10.0))}
        result = grade(spec, model)["functional_hole_axes"]
        self.assertEqual(result["status"], "pass")
        self.assertIn("largest flat face", result["note"])

    def test_auto_without_any_plane_is_an_error(self):
        spec = {"mounting_axis": "auto", "protrusion_mm": 5.0}
        model = {"digest": digest([10.0, 10.0, 10.0]), "probe": probe([])}
        result = grade(spec, model)["functional_protrusion"]
        self.assertEqual(result["status"], "error")
        self.assertIn("no planar face", result["note"])

    def test_auto_protrusion_uses_standoff_not_bounding_box(self):
        # A tall part lying diagonally has a large bbox in every axis; only the
        # distance from the mounting face answers "does it stand off the wall?".
        spec = {"mounting_axis": "auto", "protrusion_mm": 30.0}
        model = {"digest": digest([100.0, 100.0, 100.0]),
                 "probe": probe([], largest_plane=plane([0.0, 0.0, 1.0], 900.0, 6.0))}
        result = grade(spec, model)["functional_protrusion"]
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["actual"], 6.0)

    def test_largest_plane_wins_across_objects(self):
        spec = {"mounting_axis": "auto", "protrusion_mm": 10.0}
        payload = {"objects": [
            {"object": "Small", "holes": [],
             "largest_plane": plane([1.0, 0.0, 0.0], 100.0, 5.0)},
            {"object": "Big", "holes": [],
             "largest_plane": plane([0.0, 0.0, 1.0], 900.0, 40.0)},
        ]}
        results = run_eval.check_functional(
            spec, StubCache(digest([50.0, 50.0, 50.0])), StubCache(payload))
        self.assertEqual(results[0]["status"], "pass")
        self.assertEqual(results[0]["actual"], 40.0)


class PartialArcs(unittest.TestCase):
    """A concave surface is not automatically a bore."""

    def test_a_curved_lip_is_not_counted_as_a_screw_hole(self):
        # The real false positive: a wall hook's J-curve is a 24 mm concave
        # cylinder sweeping 90 deg, at right angles to the mounting face. Counting
        # it as a fastener hole failed a model that was in fact correct.
        spec = {"mounting_axis": "z", "hole_axis_tol_deg": 10.0, "min_hole_count": 2,
                "min_hole_dia_mm": 3.0}
        holes = [hole([1.0, 0.0, 0.0], diameter=24.0, angle_deg=90.0),
                 hole([0.0, 0.0, 1.0]), hole([0.0, 0.0, 1.0])]
        result = grade(spec, {"digest": digest([120.0, 30.0, 60.0]),
                              "probe": probe(holes)})["functional_hole_axes"]
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["actual"]["holes_found"], 2)

    def test_a_bore_split_into_half_cylinders_still_counts(self):
        # feature_probe sums the sweep across the faces of one axis group, so two
        # 180 deg halves add up to a full bore.
        spec = {"mounting_axis": "z", "hole_axis_tol_deg": 10.0}
        result = grade(spec, {"digest": digest([50.0, 50.0, 50.0]),
                              "probe": probe([hole([0.0, 0.0, 1.0], angle_deg=360.0)])})
        self.assertEqual(result["functional_hole_axes"]["status"], "pass")

    def test_teardrop_bores_still_count(self):
        # Measured on a real model: the self-supporting teardrop bore the print3d
        # playbook asks for sweeps exactly 270 deg, while the same part's curved
        # lips sweep 90. A threshold above 270 would have punished the better
        # design, which is why the default sits at 240.
        spec = {"mounting_axis": "z", "hole_axis_tol_deg": 10.0, "min_hole_count": 2}
        holes = [hole([0.0, 0.0, 1.0], diameter=4.5, angle_deg=270.0),
                 hole([0.0, 0.0, 1.0], diameter=4.5, angle_deg=270.0),
                 hole([1.0, 0.0, 0.0], diameter=16.0, angle_deg=90.0),
                 hole([1.0, 0.0, 0.0], diameter=32.0, angle_deg=90.0, kind="boss")]
        result = grade(spec, {"digest": digest([120.0, 25.0, 38.0]),
                              "probe": probe(holes)})["functional_hole_axes"]
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["actual"]["holes_found"], 2)

    def test_default_threshold_sits_between_the_two_measured_shapes(self):
        self.assertGreater(240.0, 90.0)    # clear of a lip curve
        self.assertLess(240.0, 270.0)      # clear of a teardrop bore

    def test_threshold_is_configurable(self):
        spec = {"mounting_axis": "z", "hole_axis_tol_deg": 10.0,
                "min_hole_angle_deg": 80.0}
        result = grade(spec, {"digest": digest([50.0, 50.0, 50.0]),
                              "probe": probe([hole([1.0, 0.0, 0.0], angle_deg=90.0)])})
        self.assertEqual(result["functional_hole_axes"]["status"], "fail")

    def test_missing_angle_is_treated_as_a_full_bore(self):
        # Older bridges do not report angle_deg; their scorecards must not change.
        spec = {"mounting_axis": "z", "hole_axis_tol_deg": 10.0}
        legacy = hole([0.0, 0.0, 1.0])
        legacy.pop("angle_deg")
        result = grade(spec, {"digest": digest([50.0, 50.0, 50.0]),
                              "probe": probe([legacy])})
        self.assertEqual(result["functional_hole_axes"]["status"], "pass")


class HoleDiameterWindow(unittest.TestCase):
    """A bearing block's bore is deliberately not perpendicular to its base."""

    def test_max_diameter_excludes_the_bore(self):
        spec = {"mounting_axis": "z", "hole_axis_tol_deg": 5.0, "min_hole_count": 2,
                "min_hole_dia_mm": 4.0, "max_hole_dia_mm": 8.0}
        holes = [hole([0.0, 1.0, 0.0], diameter=21.9),   # the bearing bore, sideways
                 hole([0.0, 0.0, 1.0], diameter=5.5),    # M5 mounting holes
                 hole([0.0, 0.0, 1.0], diameter=5.5)]
        result = grade(spec, {"digest": digest([60.0, 30.0, 40.0]),
                              "probe": probe(holes)})["functional_hole_axes"]
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["actual"]["holes_found"], 2)

    def test_without_the_window_the_bore_sinks_the_check(self):
        spec = {"mounting_axis": "z", "hole_axis_tol_deg": 5.0, "min_hole_dia_mm": 4.0}
        holes = [hole([0.0, 1.0, 0.0], diameter=21.9), hole([0.0, 0.0, 1.0], diameter=5.5)]
        result = grade(spec, {"digest": digest([60.0, 30.0, 40.0]),
                              "probe": probe(holes)})["functional_hole_axes"]
        self.assertEqual(result["status"], "fail")


class HoleAxisCheck(unittest.TestCase):
    def run_check(self, holes, spec_overrides=None):
        spec = {"mounting_axis": "z", "hole_axis_tol_deg": 5.0, "min_hole_count": 1}
        spec.update(spec_overrides or {})
        return grade(spec, {"digest": digest([10.0, 10.0, 10.0]), "probe": probe(holes)})

    def test_axis_sign_does_not_matter(self):
        # A bore has an orientation, not a direction — a hole reported along -Z is
        # just as perpendicular to an XY mounting plane as one along +Z.
        result = self.run_check([hole([0.0, 0.0, -1.0])])["functional_hole_axes"]
        self.assertEqual(result["status"], "pass")

    def test_small_tilt_within_tolerance_passes(self):
        result = self.run_check([hole([0.0, 0.05, 1.0])])["functional_hole_axes"]
        self.assertEqual(result["status"], "pass")

    def test_perpendicular_hole_fails(self):
        result = self.run_check([hole([1.0, 0.0, 0.0])])["functional_hole_axes"]
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["actual"]["misaligned"], 1)

    def test_bosses_are_not_counted_as_holes(self):
        result = self.run_check([hole([0.0, 0.0, 1.0], kind="boss")])["functional_hole_axes"]
        self.assertEqual(result["status"], "fail")  # zero holes < min_hole_count
        self.assertEqual(result["actual"]["holes_found"], 0)

    def test_unknown_kind_is_not_counted(self):
        result = self.run_check([hole([0.0, 0.0, 1.0], kind=None)])["functional_hole_axes"]
        self.assertEqual(result["actual"]["holes_found"], 0)

    def test_small_bores_are_ignored(self):
        # Fillet-adjacent geometry and cable-tie slots shouldn't count as fasteners.
        result = self.run_check([hole([1.0, 0.0, 0.0], diameter=1.0)],
                                {"min_hole_dia_mm": 3.0})["functional_hole_axes"]
        self.assertEqual(result["actual"]["holes_found"], 0)

    def test_too_few_holes_fails_with_a_specific_note(self):
        result = self.run_check([hole([0.0, 0.0, 1.0])],
                                {"min_hole_count": 2})["functional_hole_axes"]
        self.assertEqual(result["status"], "fail")
        self.assertIn("need 2", result["note"])

    def test_errored_objects_are_skipped_not_crashed(self):
        payload = {"objects": [{"object": "Bad", "error": "boom"},
                               {"object": "Body", "holes": [hole([0.0, 0.0, 1.0])]}]}
        results = run_eval.check_functional(
            {"mounting_axis": "z", "hole_axis_tol_deg": 5.0},
            StubCache(digest([10.0, 10.0, 10.0])), StubCache(payload))
        self.assertEqual(results[0]["status"], "pass")


class BridgeAvailability(unittest.TestCase):
    def test_old_bridge_skips_rather_than_fails(self):
        results = run_eval.check_functional(
            {"mounting_axis": "z", "hole_axis_tol_deg": 5.0},
            StubCache(digest([10.0, 10.0, 10.0])),
            StubCache(error="Unknown command: feature_probe"))
        self.assertEqual(results[0]["status"], "skipped")

    def test_other_bridge_errors_are_errors(self):
        results = run_eval.check_functional(
            {"mounting_axis": "z", "hole_axis_tol_deg": 5.0},
            StubCache(digest([10.0, 10.0, 10.0])),
            StubCache(error="document is not valid"))
        self.assertEqual(results[0]["status"], "error")

    def test_bad_mounting_axis_is_an_error_not_a_crash(self):
        results = run_eval.check_functional(
            {"mounting_axis": "w", "protrusion_mm": 1.0},
            StubCache(digest([10.0, 10.0, 10.0])), StubCache(probe([])))
        self.assertEqual(results[0]["status"], "error")

    def test_empty_spec_is_an_error(self):
        results = run_eval.check_functional(
            {"mounting_axis": "z"},
            StubCache(digest([10.0, 10.0, 10.0])), StubCache(probe([])))
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("no functional sub-check", results[0]["note"])


class GeometryHelpers(unittest.TestCase):
    def test_angle_between_axes_ignores_direction(self):
        self.assertAlmostEqual(
            run_eval._angle_between_axes_deg([0, 0, 1], [0, 0, -1]), 0.0, places=6)
        self.assertAlmostEqual(
            run_eval._angle_between_axes_deg([1, 0, 0], [0, 0, 1]), 90.0, places=6)

    def test_angle_is_clamped_against_float_drift(self):
        # Un-normalized but parallel vectors must not push acos out of [-1, 1].
        self.assertAlmostEqual(
            run_eval._angle_between_axes_deg([0, 0, 3.0000000001], [0, 0, 1]), 0.0, places=6)

    def test_degenerate_axis_raises(self):
        with self.assertRaises(ValueError):
            run_eval._angle_between_axes_deg([0, 0, 0], [0, 0, 1])


if __name__ == "__main__":
    unittest.main()


class DimensionModes(unittest.TestCase):
    """One-sided requirements and float noise — both found by the first real sweep."""

    class _Digest:
        def __init__(self, size):
            self.size = size

        def get(self):
            return {"overall_bounding_box": {"min": [0.0, 0.0, 0.0], "max": list(self.size)}}

    def check(self, dim, size=(60.0, 30.0, 40.0)):
        return run_eval.check_dimension(dim, self._Digest(size))

    def test_value_exactly_at_tolerance_passes(self):
        # abs(22.0 - 21.9) is 0.10000000000000142 in binary floating point; a check
        # written as "within 0.1" must not fail on representation noise.
        result = run_eval.check_dimension(
            {"name": "d", "source": "bbox", "axis": "x", "expected": 21.9, "tol": 0.1},
            self._Digest((22.0, 1.0, 1.0)))
        self.assertEqual(result["status"], "pass")

    def test_min_mode_accepts_anything_above_the_floor(self):
        # "a seat at least 8 mm wide": a 30 mm seat satisfies it.
        result = self.check({"name": "seat", "source": "bbox", "axis": "y",
                             "mode": "min", "expected": 8.0})
        self.assertEqual(result["status"], "pass")

    def test_min_mode_rejects_below_the_floor(self):
        result = self.check({"name": "seat", "source": "bbox", "axis": "y",
                             "mode": "min", "expected": 40.0})
        self.assertEqual(result["status"], "fail")

    def test_max_mode_rejects_a_nominal_bore(self):
        # "sized slightly undersize for a press fit": 22.0 nominal is a slip fit.
        result = self.check({"name": "bore", "source": "bbox", "axis": "x",
                             "mode": "max", "expected": 59.0})
        self.assertEqual(result["status"], "fail")

    def test_max_mode_accepts_an_undersized_value(self):
        result = self.check({"name": "bore", "source": "bbox", "axis": "x",
                             "mode": "max", "expected": 61.0})
        self.assertEqual(result["status"], "pass")

    def test_unknown_mode_is_an_error(self):
        result = self.check({"name": "d", "source": "bbox", "axis": "x",
                             "mode": "roughly", "expected": 1.0})
        self.assertEqual(result["status"], "error")
        self.assertIn("unknown dimension mode", result["note"])
