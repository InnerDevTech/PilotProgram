"""Tests for the camera-to-desk maths. No camera and no MediaPipe needed.

Run them from the project root:

    python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.desk_geometry import (  # noqa: E402
    PresenceGate,
    Smoother,
    apply_homography,
    corners_to_homography,
    load_calibration,
    on_desk,
    save_calibration,
)

# A camera looking straight down, desk filling pixels 100..500 across and
# 50..350 down. Nothing wonky, so the answers are easy to check by hand.
SQUARE_ON = [(100, 50), (500, 50), (500, 350), (100, 350)]

# The same desk seen from an angle: the far edge is narrower than the near
# edge, which is what the real overhead webcam actually gives you.
AT_AN_ANGLE = [(180, 60), (440, 60), (520, 360), (100, 360)]


class HomographyTests(unittest.TestCase):

    def assertPointAlmostEqual(self, got, want, places=6):
        self.assertAlmostEqual(got[0], want[0], places=places)
        self.assertAlmostEqual(got[1], want[1], places=places)

    def test_corners_land_on_the_desk_corners(self):
        matrix = corners_to_homography(SQUARE_ON)

        self.assertPointAlmostEqual(apply_homography(matrix, (100, 50)), (0, 0))
        self.assertPointAlmostEqual(apply_homography(matrix, (500, 50)), (1, 0))
        self.assertPointAlmostEqual(apply_homography(matrix, (500, 350)), (1, 1))
        self.assertPointAlmostEqual(apply_homography(matrix, (100, 350)), (0, 1))

    def test_middle_maps_to_the_middle(self):
        matrix = corners_to_homography(SQUARE_ON)
        self.assertPointAlmostEqual(apply_homography(matrix, (300, 200)), (0.5, 0.5))

    def test_angled_corners_still_land_on_the_corners(self):
        matrix = corners_to_homography(AT_AN_ANGLE)

        for pixel, desk in zip(AT_AN_ANGLE, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            self.assertPointAlmostEqual(apply_homography(matrix, pixel), desk)

    def test_angled_view_is_not_just_a_stretch(self):
        # With the camera at an angle, the middle of the picture is NOT the
        # middle of the desk. If this ever came out as (0.5, 0.5) it would
        # mean the perspective part of the homography had been lost.
        matrix = corners_to_homography(AT_AN_ANGLE)
        x, y = apply_homography(matrix, (310, 210))

        self.assertTrue(0 < x < 1 and 0 < y < 1)
        self.assertNotAlmostEqual(y, 0.5, places=2)

    def test_a_point_outside_the_desk_maps_outside_0_to_1(self):
        matrix = corners_to_homography(SQUARE_ON)
        x, y = apply_homography(matrix, (40, 200))

        self.assertLess(x, 0)
        self.assertFalse(on_desk((x, y)))

    def test_wrong_number_of_corners(self):
        with self.assertRaises(ValueError):
            corners_to_homography(SQUARE_ON[:3])

    def test_corners_in_a_line_are_rejected(self):
        with self.assertRaises(ValueError):
            corners_to_homography([(0, 0), (10, 0), (20, 0), (30, 0)])


class OnDeskTests(unittest.TestCase):

    def test_inside_and_outside(self):
        self.assertTrue(on_desk((0.5, 0.5)))
        self.assertTrue(on_desk((0.0, 1.0)))
        self.assertFalse(on_desk((1.2, 0.5)))
        self.assertFalse(on_desk((0.5, -0.1)))

    def test_margin_allows_a_little_overhang(self):
        self.assertFalse(on_desk((1.05, 0.5)))
        self.assertTrue(on_desk((1.05, 0.5), margin=0.1))


class CalibrationFileTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "calibration.json"
        self.addCleanup(self.dir.cleanup)

    def test_save_then_load_round_trip(self):
        save_calibration(AT_AN_ANGLE, (640, 480), path=self.path)
        matrix, frame_size = load_calibration(path=self.path)

        self.assertEqual(frame_size, (640, 480))
        x, y = apply_homography(matrix, AT_AN_ANGLE[2])
        self.assertAlmostEqual(x, 1.0, places=6)
        self.assertAlmostEqual(y, 1.0, places=6)

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_calibration(path=self.path)

    def test_file_that_is_not_json(self):
        self.path.write_text("not json at all", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_calibration(path=self.path)

    def test_file_missing_a_field(self):
        self.path.write_text(json.dumps({"corners": SQUARE_ON}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_calibration(path=self.path)


class SmootherTests(unittest.TestCase):

    def test_first_reading_is_taken_as_it_is(self):
        self.assertEqual(Smoother(0.5).update((0.2, 0.8)), (0.2, 0.8))

    def test_it_moves_part_of_the_way(self):
        smoother = Smoother(0.5)
        smoother.update((0.0, 0.0))
        self.assertEqual(smoother.update((1.0, 1.0)), (0.5, 0.5))

    def test_weight_of_one_does_no_smoothing(self):
        smoother = Smoother(1.0)
        smoother.update((0.0, 0.0))
        self.assertEqual(smoother.update((0.3, 0.7)), (0.3, 0.7))

    def test_it_settles_on_a_held_position(self):
        smoother = Smoother(0.5)
        smoother.update((0.0, 0.0))
        for _ in range(40):
            got = smoother.update((1.0, 1.0))
        self.assertAlmostEqual(got[0], 1.0, places=6)

    def test_reset_forgets_the_last_position(self):
        smoother = Smoother(0.5)
        smoother.update((0.0, 0.0))
        smoother.reset()
        self.assertEqual(smoother.update((1.0, 1.0)), (1.0, 1.0))

    def test_bad_weight(self):
        for bad in (0, -0.5, 1.5):
            with self.assertRaises(ValueError):
                Smoother(bad)


class PresenceGateTests(unittest.TestCase):

    def test_it_takes_two_frames_to_appear(self):
        gate = PresenceGate(appear_frames=2, vanish_frames=6)

        self.assertFalse(gate.update(True))
        self.assertTrue(gate.update(True))

    def test_one_dropped_frame_does_not_blink_the_dot_out(self):
        gate = PresenceGate(appear_frames=2, vanish_frames=6)
        gate.update(True)
        gate.update(True)

        self.assertTrue(gate.update(False))
        self.assertTrue(gate.update(True))

    def test_it_takes_six_missed_frames_to_vanish(self):
        gate = PresenceGate(appear_frames=2, vanish_frames=6)
        gate.update(True)
        gate.update(True)

        for _ in range(5):
            self.assertTrue(gate.update(False))
        self.assertFalse(gate.update(False))

    def test_a_single_false_detection_never_shows_a_dot(self):
        # This is the Sprint 2 "no false hands" test, in miniature: one noisy
        # frame on an empty desk must not be enough to put a dot up.
        gate = PresenceGate(appear_frames=2, vanish_frames=6)

        for _ in range(20):
            self.assertFalse(gate.update(False))
        self.assertFalse(gate.update(True))

    def test_bad_frame_counts(self):
        with self.assertRaises(ValueError):
            PresenceGate(appear_frames=0)
        with self.assertRaises(ValueError):
            PresenceGate(vanish_frames=0)


if __name__ == "__main__":
    unittest.main()
