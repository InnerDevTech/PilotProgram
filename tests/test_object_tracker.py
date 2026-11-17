"""Tests for the object sensor. No camera needed - the frames are painted here.

A "desk" is a plain grey picture and an "object" is a coloured rectangle drawn
on it, which is all background subtraction needs to be put through its paces.
The clock is made up too, so half a second passes instantly.

Run them from the project root:

    python -m pytest tests
"""

import sys
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import desk_geometry, object_tracker  # noqa: E402
from bridge.object_tracker import (  # noqa: E402
    APPEAR_SECONDS, VANISH_SECONDS, BackgroundModel, Blob, ObjectDetector,
    ObjectTracker, clean_mask, find_blobs, hand_mask, objects_event)

HAS_CV2 = object_tracker.cv2 is not None
FRAME = (640, 480)
FRAME_TIME = 1 / 30


def desk():
    """An empty grey desk."""
    return np.full((FRAME[1], FRAME[0], 3), 120, dtype=np.uint8)


def carpet():
    """A speckled grey desk, like carpet: the texture is what shadows keep."""
    rng = np.random.default_rng(7)
    grain = rng.integers(70, 170, (FRAME[1], FRAME[0], 1)).astype(np.uint8)
    return np.repeat(cv2.GaussianBlur(grain, (3, 3), 0)[..., None], 3, axis=2)


def shaded(frame, x0, x1, keep=0.6):
    """The same picture with a shadow over columns x0 to x1."""
    frame = frame.astype(np.float32)
    frame[:, x0:x1] *= keep
    return frame.astype(np.uint8)


def with_box(frame, x, y, w=80, h=60, colour=(30, 60, 200)):
    """The same picture with a solid rectangle on it. (x, y) is its top left."""
    frame = frame.copy()
    frame[y:y + h, x:x + w] = colour
    return frame


def blob_at(x, y, size=40, colour=None):
    half = size / 2
    contour = np.array([[x - half, y - half], [x + half, y - half],
                        [x + half, y + half], [x - half, y + half]])
    return Blob((x, y), size * size, contour, colour)


def ready_detector(matrix=None, empty=None):
    """A detector that has already photographed the empty desk."""
    empty = desk() if empty is None else empty
    detector = ObjectDetector(FRAME, matrix)
    detector.recapture()
    for _ in range(detector.background.capture_frames):
        detector.process(empty, now=0.0)
    return detector


def show(detector, frame, seconds, start=0.0, hands=()):
    """Show the detector the same frame for a while. Returns (objects, end time)."""
    objects = []
    now = start
    for _ in range(int(round(seconds / FRAME_TIME))):
        now += FRAME_TIME
        objects = detector.process(frame, hands, now=now)
    return objects, now


# ----------------------------------------------------------------- tracker --

class TrackerTests(unittest.TestCase):
    """The pure logic: made-up blobs, made-up clock, no OpenCV."""

    def settle(self, tracker, blobs, start=0.0, seconds=APPEAR_SECONDS + 0.1):
        now = start
        objects = []
        while now <= start + seconds:
            objects = tracker.update(blobs, now)
            now += FRAME_TIME
        return objects, now

    def test_a_new_blob_is_not_an_object_straight_away(self):
        tracker = ObjectTracker()
        self.assertEqual(tracker.update([blob_at(100, 100)], 0.0), [])
        self.assertEqual(tracker.update([blob_at(100, 100)], 0.1), [])

    def test_a_still_blob_becomes_object_one(self):
        objects, _ = self.settle(ObjectTracker(), [blob_at(100, 100)])

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].id, 1)

    def test_a_blob_that_keeps_moving_never_becomes_an_object(self):
        # A sleeve sweeping across the desk: always there, never still.
        tracker = ObjectTracker()
        for step in range(60):
            objects = tracker.update([blob_at(50 + step * 8, 100)], step * FRAME_TIME)
            self.assertEqual(objects, [])

    def test_a_slid_object_keeps_its_number(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100)])

        for step in range(1, 30):
            now += FRAME_TIME
            objects = tracker.update([blob_at(100 + step * 10, 100)], now)

        self.assertEqual([obj.id for obj in objects], [1])
        self.assertAlmostEqual(objects[0].centre[0], 390)

    def test_two_objects_keep_their_own_numbers(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100), blob_at(400, 300)])

        # Hand the blobs over in the other order: the numbers must not swap.
        objects = tracker.update([blob_at(402, 300), blob_at(101, 100)], now)

        by_id = {obj.id: obj.centre for obj in objects}
        self.assertEqual(by_id[1], (101.0, 100.0))
        self.assertEqual(by_id[2], (402.0, 300.0))

    def test_one_missed_frame_does_not_drop_the_object(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100)])

        self.assertEqual(len(tracker.update([], now + FRAME_TIME)), 1)

    def test_a_removed_object_goes_after_the_wait(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100)])

        tracker.update([], now)
        self.assertEqual(tracker.update([], now + VANISH_SECONDS + 0.1), [])

    def test_an_object_under_a_hand_is_held(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100)])

        hands = np.zeros((FRAME[1], FRAME[0]), dtype=np.uint8)
        hands[60:140, 60:140] = 255
        for _ in range(90):                      # three seconds under the hand
            now += FRAME_TIME
            objects = tracker.update([], now, hands)

        self.assertEqual([obj.id for obj in objects], [1])

    def test_a_hand_somewhere_else_does_not_hold_it(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100)])

        hands = np.zeros((FRAME[1], FRAME[0]), dtype=np.uint8)
        hands[300:400, 400:500] = 255
        tracker.update([], now, hands)

        self.assertEqual(tracker.update([], now + VANISH_SECONDS + 0.1, hands), [])

    def test_a_new_object_gets_a_new_number(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100)])
        tracker.update([], now)
        tracker.update([], now + 2.0)

        objects, _ = self.settle(tracker, [blob_at(100, 100, size=80)], start=now + 3.0)
        self.assertEqual([obj.id for obj in objects], [2])

    def test_picked_up_and_put_down_elsewhere_keeps_its_number(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100, colour=(40, 40, 40))])
        tracker.update([], now)
        tracker.update([], now + 2.0)                 # gone: it was picked up

        objects, _ = self.settle(tracker, [blob_at(400, 300, colour=(45, 42, 38))],
                                 start=now + 4.0)
        self.assertEqual([obj.id for obj in objects], [1])

    def test_a_different_looking_thing_gets_a_new_number(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100, colour=(40, 40, 40))])
        tracker.update([], now)
        tracker.update([], now + 2.0)

        # Same size, but white instead of black.
        objects, _ = self.settle(tracker, [blob_at(400, 300, colour=(230, 230, 230))],
                                 start=now + 4.0)
        self.assertEqual([obj.id for obj in objects], [2])

    def test_an_object_gone_for_a_long_time_is_forgotten(self):
        tracker = ObjectTracker(remember_seconds=5)
        _, now = self.settle(tracker, [blob_at(100, 100)])
        tracker.update([], now)
        tracker.update([], now + 2.0)
        tracker.update([], now + 10.0)

        objects, _ = self.settle(tracker, [blob_at(100, 100)], start=now + 11.0)
        self.assertEqual([obj.id for obj in objects], [2])

    def test_two_lookalikes_do_not_share_a_number(self):
        tracker = ObjectTracker()
        _, now = self.settle(tracker, [blob_at(100, 100)])
        tracker.update([], now)
        tracker.update([], now + 2.0)

        objects, _ = self.settle(tracker, [blob_at(100, 100), blob_at(400, 300)],
                                 start=now + 3.0)
        self.assertEqual(sorted(obj.id for obj in objects), [1, 2])


# ------------------------------------------------------------------- pixels --

@unittest.skipUnless(HAS_CV2, "needs OpenCV")
class BackgroundTests(unittest.TestCase):

    def captured(self, **settings):
        model = BackgroundModel(**settings)
        model.start_capture()
        while not model.feed(desk()):
            pass
        return model

    def test_it_is_not_ready_until_enough_frames_arrive(self):
        model = BackgroundModel(capture_frames=3)
        model.start_capture()

        self.assertFalse(model.feed(desk()))
        self.assertFalse(model.ready)
        self.assertFalse(model.feed(desk()))
        self.assertTrue(model.feed(desk()))
        self.assertTrue(model.ready)

    def test_the_empty_desk_shows_no_difference(self):
        self.assertEqual(int(self.captured().difference(desk()).max()), 0)

    def test_an_object_shows_up_where_it_is(self):
        gap = self.captured().difference(with_box(desk(), 200, 150))

        self.assertGreater(int(gap[180, 240]), 60)     # inside the box
        self.assertEqual(int(gap[400, 500]), 0)        # nowhere near it

    def test_same_brightness_different_colour_still_shows(self):
        # Grey 120 and this red are equally bright in black and white.
        gap = self.captured().difference(
            with_box(desk(), 200, 150, colour=(60, 60, 255)))

        self.assertGreater(int(gap[180, 240]), 50)

    def test_slow_light_changes_are_learnt(self):
        model = self.captured(learn_rate=0.1)
        brighter = np.full_like(desk(), 140)
        everywhere = np.full(brighter.shape[:2], 255, dtype=np.uint8)

        for _ in range(100):
            model.learn(brighter, everywhere)

        self.assertLess(int(model.difference(brighter).max()), 3)

    def test_nothing_is_learnt_where_it_is_not_allowed(self):
        model = self.captured(learn_rate=0.5)
        frame = with_box(desk(), 200, 150)
        nowhere = np.zeros(frame.shape[:2], dtype=np.uint8)

        for _ in range(50):
            model.learn(frame, nowhere)

        self.assertGreater(int(model.difference(frame)[180, 240]), 60)


@unittest.skipUnless(HAS_CV2, "needs OpenCV")
class BlobTests(unittest.TestCase):

    def mask_with(self, *boxes):
        mask = np.zeros((FRAME[1], FRAME[0]), dtype=np.uint8)
        for x, y, w, h in boxes:
            mask[y:y + h, x:x + w] = 255
        return mask

    def test_one_box_is_one_blob_at_its_centre(self):
        blobs = find_blobs(self.mask_with((200, 150, 80, 60)))

        self.assertEqual(len(blobs), 1)
        self.assertAlmostEqual(blobs[0].centre[0], 239.5, delta=1)
        self.assertAlmostEqual(blobs[0].centre[1], 179.5, delta=1)

    def test_a_long_thin_strip_is_ignored(self):
        # The edge of a sharp shadow: big enough, but far too thin.
        self.assertEqual(find_blobs(self.mask_with((200, 50, 8, 300))), [])

    def test_blobs_get_their_colour_when_given_the_frame(self):
        frame = with_box(desk(), 200, 150, colour=(30, 60, 200))
        blob = find_blobs(self.mask_with((200, 150, 80, 60)), frame=frame)[0]

        self.assertEqual([round(c) for c in blob.colour], [30, 60, 200])

    def test_small_specks_are_ignored(self):
        self.assertEqual(find_blobs(self.mask_with((200, 150, 10, 10))), [])

    def test_a_blob_touching_the_edge_is_ignored(self):
        # An arm reaching in from the side of the picture.
        self.assertEqual(find_blobs(self.mask_with((0, 200, 300, 60))), [])

    def test_speckle_is_cleaned_out_of_the_mask(self):
        gap = np.zeros((FRAME[1], FRAME[0]), dtype=np.uint8)
        gap[100, 100] = 255
        gap[300:360, 300:380] = 255

        mask = clean_mask(gap)

        self.assertEqual(int(mask[100, 100]), 0)
        self.assertEqual(int(mask[330, 340]), 255)

    def test_a_hand_mask_covers_the_hand_and_a_bit_more(self):
        hand = [(300, 200), (360, 200), (360, 280), (300, 280)]
        mask = hand_mask((FRAME[1], FRAME[0]), [hand], padding=20)

        self.assertEqual(int(mask[240, 330]), 255)     # middle of the hand
        self.assertEqual(int(mask[240, 372]), 255)     # inside the padding
        self.assertEqual(int(mask[240, 420]), 0)       # well clear of it

    def test_no_hands_is_an_empty_mask(self):
        self.assertEqual(int(hand_mask((FRAME[1], FRAME[0]), []).max()), 0)


# ---------------------------------------------------------- the whole thing --

@unittest.skipUnless(HAS_CV2, "needs OpenCV")
class DetectorTests(unittest.TestCase):
    """Painted frames through the real pipeline, start to finish."""

    def test_an_empty_desk_has_no_objects(self):
        objects, _ = show(ready_detector(), desk(), 2.0)
        self.assertEqual(objects, [])

    def test_nothing_is_reported_while_the_desk_is_being_photographed(self):
        detector = ObjectDetector(FRAME)
        detector.recapture()

        self.assertEqual(detector.process(with_box(desk(), 200, 150), now=0.0), [])
        self.assertFalse(detector.ready)

    def test_an_object_appears_within_a_second(self):
        objects, _ = show(ready_detector(), with_box(desk(), 200, 150), 1.0)

        self.assertEqual(len(objects), 1)
        self.assertAlmostEqual(objects[0].centre[0], 240, delta=2)
        self.assertAlmostEqual(objects[0].centre[1], 180, delta=2)

    def test_the_outline_follows_a_slid_object(self):
        detector = ready_detector()
        _, now = show(detector, with_box(desk(), 200, 150), 1.0)

        for step in range(1, 21):
            now += FRAME_TIME
            objects = detector.process(with_box(desk(), 200 + step * 10, 150), now=now)

        self.assertEqual([obj.id for obj in objects], [1])
        self.assertAlmostEqual(objects[0].centre[0], 440, delta=2)

    def test_a_removed_object_disappears(self):
        detector = ready_detector()
        _, now = show(detector, with_box(desk(), 200, 150), 1.0)

        objects, _ = show(detector, desk(), 1.0, start=now)
        self.assertEqual(objects, [])

    def test_a_hand_is_not_an_object(self):
        # A hand-coloured patch in the middle of the desk, with landmarks on it.
        frame = with_box(desk(), 300, 200, w=70, h=90, colour=(150, 170, 220))
        hand = [(300, 200), (370, 200), (370, 290), (300, 290)]

        objects, _ = show(ready_detector(), frame, 2.0, hands=[hand])
        self.assertEqual(objects, [])

    def test_an_arm_from_the_edge_is_not_an_object(self):
        # No landmarks at all - MediaPipe missed it - but it touches the edge.
        frame = with_box(desk(), 0, 200, w=300, h=70, colour=(150, 170, 220))

        objects, _ = show(ready_detector(), frame, 2.0)
        self.assertEqual(objects, [])

    def test_a_hand_passing_over_an_object_does_not_remove_it(self):
        detector = ready_detector()
        mug = with_box(desk(), 200, 150)
        _, now = show(detector, mug, 1.0)

        covered = with_box(mug, 190, 140, w=100, h=80, colour=(150, 170, 220))
        hand = [(190, 140), (290, 140), (290, 220), (190, 220)]
        objects, now = show(detector, covered, 2.0, start=now, hands=[hand])
        self.assertEqual([obj.id for obj in objects], [1])

        objects, _ = show(detector, mug, 0.5, start=now)
        self.assertEqual([obj.id for obj in objects], [1])

    def test_an_object_left_on_the_desk_does_not_fade(self):
        # The background keeps learning; the mug must not be learnt with it.
        objects, _ = show(ready_detector(), with_box(desk(), 200, 150), 10.0)
        self.assertEqual(len(objects), 1)

    def test_the_room_slowly_getting_brighter_makes_no_objects(self):
        detector = ready_detector()
        now = 0.0
        for step in range(600):                   # 20 seconds, +30 brightness
            now += FRAME_TIME
            frame = np.full_like(desk(), 120 + step // 20)
            self.assertEqual(detector.process(frame, now=now), [])

    def test_the_lights_going_out_is_flagged_not_reported(self):
        detector = ready_detector()
        dark = np.full_like(desk(), 20)

        objects, _ = show(detector, dark, 1.0)

        self.assertEqual(objects, [])
        self.assertTrue(detector.flooded)

    def test_photographing_again_clears_everything(self):
        detector = ready_detector()
        mug = with_box(desk(), 200, 150)
        show(detector, mug, 1.0)

        # Recapture with the mug still there: it is now part of the empty desk.
        detector.recapture()
        for _ in range(detector.background.capture_frames):
            detector.process(mug, now=5.0)
        objects, _ = show(detector, mug, 1.0, start=5.0)

        self.assertEqual(objects, [])

    def test_a_shadow_on_the_empty_desk_is_not_an_object(self):
        detector = ready_detector(empty=carpet())

        objects, _ = show(detector, shaded(carpet(), 250, 400), 2.0)
        self.assertEqual(objects, [])

    def test_a_dark_object_on_carpet_survives_a_shadow(self):
        # The bug this guards: a black remote on grey carpet vanished as soon
        # as a shadow fell over it, because shadow and remote are both dark.
        detector = ready_detector(empty=carpet())
        remote = with_box(carpet(), 280, 180, w=50, h=140, colour=(40, 40, 45))
        _, now = show(detector, remote, 1.0)

        objects, _ = show(detector, shaded(remote, 220, 420), 2.0, start=now)
        self.assertEqual([obj.id for obj in objects], [1])

    def test_a_parked_shadow_is_not_learnt_into_the_desk(self):
        # Learning the shadow would leave a "ghost" of its edge behind.
        detector = ready_detector(empty=carpet())
        _, now = show(detector, shaded(carpet(), 250, 400), 6.0)

        objects, _ = show(detector, carpet(), 1.0, start=now)
        self.assertEqual(objects, [])

    def test_things_beside_the_projection_are_ignored(self):
        # Calibrated so the desk is only the middle of the picture.
        matrix = desk_geometry.corners_to_homography(
            [(200, 100), (500, 100), (500, 400), (200, 400)])

        outside, _ = show(ready_detector(matrix), with_box(desk(), 40, 200), 1.0)
        inside, _ = show(ready_detector(matrix), with_box(desk(), 300, 200), 1.0)

        self.assertEqual(outside, [])
        self.assertEqual(len(inside), 1)


@unittest.skipUnless(HAS_CV2, "needs OpenCV")
class EventTests(unittest.TestCase):

    def one_object(self):
        objects, _ = show(ready_detector(), with_box(desk(), 280, 210, w=80, h=60), 1.0)
        return objects

    def test_no_objects_is_an_empty_list(self):
        self.assertEqual(objects_event([], FRAME),
                         {"type": "objects", "objects": []})

    def test_without_calibration_it_is_a_fraction_of_the_picture(self):
        event = objects_event(self.one_object(), FRAME)
        item = event["objects"][0]

        self.assertEqual(item["id"], 1)
        self.assertAlmostEqual(item["x"], 0.5, delta=0.01)
        self.assertAlmostEqual(item["y"], 0.5, delta=0.01)
        self.assertGreaterEqual(len(item["outline"]), 4)

    def test_with_calibration_it_is_in_desk_coordinates(self):
        # The desk is the left half of the picture, so the picture's middle
        # is the desk's right-hand edge.
        matrix = desk_geometry.corners_to_homography(
            [(0, 0), (320, 0), (320, 480), (0, 480)])
        item = objects_event(self.one_object(), FRAME, matrix)["objects"][0]

        self.assertAlmostEqual(item["x"], 1.0, delta=0.01)
        self.assertAlmostEqual(item["y"], 0.5, delta=0.01)


if __name__ == "__main__":
    unittest.main()
