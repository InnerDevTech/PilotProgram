"""Tests for the hand tracker's logic. No camera and no model file needed.

The camera loop itself cannot be tested without hardware, so everything that
can be decided without a picture - where the palm is, when to send a message,
when to shut up - was kept out of the loop and is tested here.

Run them from the project root:

    python -m unittest discover -s tests -v
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import desk_geometry  # noqa: E402
from bridge.hand_tracker import MOVE_THRESHOLD, PALM_LANDMARKS, HandTracker  # noqa: E402

# The same square-on camera view the geometry tests use: desk fills pixels
# 100..500 across and 50..350 down, in a 600x400 frame.
SQUARE_ON = [(100, 50), (500, 50), (500, 350), (100, 350)]
FRAME = (600, 400)


class FakeLandmark:
    """One MediaPipe landmark: x and y as a fraction of the frame."""

    def __init__(self, x, y):
        self.x = x
        self.y = y


def hand_at(pixel_x, pixel_y, frame=FRAME):
    """Build a 21-landmark hand whose palm sits on one camera pixel.

    Only landmarks 0, 5 and 17 matter (hand_tracker averages those three),
    so they all go on the wanted spot and the rest are parked elsewhere to
    prove they are genuinely ignored.
    """
    marks = [FakeLandmark(0.99, 0.99) for _ in range(21)]
    for index in PALM_LANDMARKS:
        marks[index] = FakeLandmark(pixel_x / frame[0], pixel_y / frame[1])
    return marks


class RecordingBroadcaster:
    """Collects what would have gone to the browser tabs."""

    def __init__(self):
        self.events = []

    def send(self, event):
        self.events.append(event)

    def hand_events(self):
        return [e for e in self.events if e.get("type") == "hand"]


def make_tracker():
    tracker = HandTracker(RecordingBroadcaster())
    tracker._matrix = desk_geometry.corners_to_homography(SQUARE_ON)
    return tracker


class FrameToDeskTests(unittest.TestCase):

    def test_palm_in_the_middle_of_the_desk(self):
        tracker = make_tracker()
        x, y = tracker.frame_to_desk(hand_at(300, 200), *FRAME)

        self.assertAlmostEqual(x, 0.5, places=6)
        self.assertAlmostEqual(y, 0.5, places=6)

    def test_palm_at_a_desk_corner(self):
        tracker = make_tracker()
        x, y = tracker.frame_to_desk(hand_at(100, 50), *FRAME)

        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)

    def test_only_the_palm_landmarks_are_used(self):
        # Every other landmark is parked at the bottom right of the frame.
        # If any of them leaked into the average, this would not be 0.5.
        tracker = make_tracker()
        x, y = tracker.frame_to_desk(hand_at(300, 200), *FRAME)

        self.assertAlmostEqual(x, 0.5, places=6)
        self.assertAlmostEqual(y, 0.5, places=6)

    def test_a_hand_beside_the_desk_maps_outside(self):
        tracker = make_tracker()
        point = tracker.frame_to_desk(hand_at(550, 200), *FRAME)

        self.assertFalse(desk_geometry.on_desk(point))

    def test_without_calibration_it_refuses(self):
        tracker = HandTracker(RecordingBroadcaster())
        with self.assertRaises(ValueError):
            tracker.frame_to_desk(hand_at(300, 200), *FRAME)


class PublishTests(unittest.TestCase):

    def test_a_position_is_sent(self):
        tracker = make_tracker()
        tracker._publish((0.25, 0.75))

        self.assertEqual(tracker.broadcaster.hand_events(),
                         [{"type": "hand", "x": 0.25, "y": 0.75}])

    def test_a_still_hand_is_not_resent_every_frame(self):
        # 30 frames a second of "still at 0.5, 0.5" would be 30 pointless
        # messages a second down every open tab.
        tracker = make_tracker()
        for _ in range(30):
            tracker._publish((0.5, 0.5))

        self.assertEqual(len(tracker.broadcaster.hand_events()), 1)

    def test_a_tiny_wobble_is_not_resent(self):
        tracker = make_tracker()
        tracker._publish((0.5, 0.5))
        tracker._publish((0.5 + MOVE_THRESHOLD / 2, 0.5))

        self.assertEqual(len(tracker.broadcaster.hand_events()), 1)

    def test_a_real_move_is_sent(self):
        tracker = make_tracker()
        tracker._publish((0.5, 0.5))
        tracker._publish((0.5 + MOVE_THRESHOLD * 2, 0.5))

        self.assertEqual(len(tracker.broadcaster.hand_events()), 2)

    def test_the_desk_going_clear_is_sent_once(self):
        tracker = make_tracker()
        tracker._publish((0.5, 0.5))
        for _ in range(10):
            tracker._publish(None)

        events = tracker.broadcaster.hand_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1], {"type": "hand", "present": False})

    def test_an_empty_desk_sends_nothing_at_all(self):
        # Nothing has ever been on the desk, so there is nothing to clear.
        tracker = make_tracker()
        for _ in range(10):
            tracker._publish(None)

        self.assertEqual(tracker.broadcaster.hand_events(), [])

    def test_a_hand_coming_back_is_sent_again(self):
        tracker = make_tracker()
        tracker._publish((0.5, 0.5))
        tracker._publish(None)
        tracker._publish((0.5, 0.5))

        events = tracker.broadcaster.hand_events()
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1], {"type": "hand", "x": 0.5, "y": 0.5})

    def test_positions_are_rounded_before_sending(self):
        tracker = make_tracker()
        tracker._publish((0.123456789, 0.987654321))

        event = tracker.broadcaster.hand_events()[0]
        self.assertEqual(event["x"], 0.1235)
        self.assertEqual(event["y"], 0.9877)


class StatusTests(unittest.TestCase):

    def test_status_before_it_starts(self):
        tracker = HandTracker(RecordingBroadcaster())
        status = tracker.status_event()

        self.assertEqual(status["type"], "hand_status")
        self.assertFalse(status["connected"])

    def test_a_missing_calibration_is_reported_not_raised(self):
        # A camera problem must never take the clock and timer down with it,
        # so _run reports and returns instead of throwing.
        tracker = HandTracker(RecordingBroadcaster(),
                              calibration_path=ROOT / "no-such-file.json")
        tracker._matrix = None

        # _fail prints for the person running the bridge; swallow that here
        # so a passing test suite stays quiet.
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tracker._fail("nope"), 1)
        self.assertFalse(tracker.connected)
        self.assertEqual(tracker.broadcaster.events[-1]["type"], "hand_status")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------- the loop, faked out --

class _FakeResult:
    def __init__(self, hand):
        self.hand_landmarks = [hand] if hand is not None else []


class _FakeLandmarker:
    """Hands out one scripted result per frame: a hand, or None for 'no hand'."""

    def __init__(self, script):
        self.script = list(script)

    def detect_for_video(self, image, stamp_ms):
        return _FakeResult(self.script.pop(0))


class _FakeCapture:
    """Gives `frames` blank frames, then tells the tracker to stop."""

    def __init__(self, tracker, frames):
        self.tracker = tracker
        self.frames = frames

    def read(self):
        if self.frames == 0:
            self.tracker.stop()
            return False, None
        self.frames -= 1
        return True, object()


class _FakeCv2:
    COLOR_BGR2RGB = 0

    @staticmethod
    def cvtColor(frame, code):
        return frame


class _FakeMp:
    class ImageFormat:
        SRGB = 0

    @staticmethod
    def Image(image_format, data):
        return data


class LoopTests(unittest.TestCase):
    """Runs the real camera loop with a scripted camera and hand detector."""

    def run_loop(self, script):
        from bridge import hand_tracker
        tracker = make_tracker()
        saved = hand_tracker.cv2, hand_tracker.mp
        hand_tracker.cv2, hand_tracker.mp = _FakeCv2, _FakeMp
        try:
            tracker._loop(_FakeCapture(tracker, len(script)),
                          _FakeLandmarker(script), *FRAME)
        finally:
            hand_tracker.cv2, hand_tracker.mp = saved
        return tracker.broadcaster.hand_events()

    def test_a_hand_leaving_does_not_crash(self):
        # The bug this guards: with the dot still showing, a frame with no
        # hand handed None to the smoother and killed the tracker thread.
        hand = hand_at(300, 200)
        events = self.run_loop([hand] * 4 + [None] * 10)
        self.assertEqual(events[-1], {"type": "hand", "present": False})

    def test_the_dot_holds_still_through_a_dropped_frame(self):
        hand = hand_at(300, 200)
        events = self.run_loop([hand] * 4 + [None] * 2 + [hand] * 4)
        self.assertNotIn({"type": "hand", "present": False}, events)

    def test_a_hand_moving_off_the_desk_does_not_crash(self):
        events = self.run_loop([hand_at(300, 200)] * 4 + [hand_at(590, 390)] * 10)
        self.assertEqual(events[-1], {"type": "hand", "present": False})
