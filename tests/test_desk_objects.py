"""Tests for the Desk screen's plumbing: objects through the camera loop and
the bridge. No camera, no MediaPipe model and no Arduino needed.

The frames are painted (a grey desk, a dark rectangle for an object) and the
clock is made up, the same way tests/test_object_tracker.py does it.

Run them from the project root:

    python -m pytest tests
"""

import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import desk_geometry, hand_tracker, object_tracker  # noqa: E402
from bridge.hand_tracker import (  # noqa: E402
    OBJECT_SEND_SECONDS, OBJECT_SETTLE_SECONDS, HandTracker)
from bridge.jarvis_bridge import Broadcaster, make_server  # noqa: E402

HAS_CV2 = object_tracker.cv2 is not None
WIDTH, HEIGHT = 640, 480
STEP = 1 / 15                  # objects are looked at every other frame


def desk():
    return np.full((HEIGHT, WIDTH, 3), 120, dtype=np.uint8)


def with_box(frame, x, y, w=80, h=60):
    frame = frame.copy()
    frame[y:y + h, x:x + w] = (30, 40, 50)
    return frame


class RecordingBroadcaster:
    def __init__(self):
        self.events = []

    def send(self, event):
        self.events.append(event)

    def of_type(self, kind):
        return [e for e in self.events if e.get("type") == kind]

    def states(self):
        return [e["state"] for e in self.of_type("objects_status")]


def make_tracker():
    tracker = HandTracker(RecordingBroadcaster())
    # The whole camera picture is the desk.
    tracker._matrix = desk_geometry.corners_to_homography(
        [(0, 0), (WIDTH, 0), (WIDTH, HEIGHT), (0, HEIGHT)])
    return tracker


class Clock:
    """Feeds frames to the tracker's object step with a made-up clock."""

    def __init__(self, tracker):
        self.tracker = tracker
        self.now = 1000.0

    def run(self, frame, seconds, hands=()):
        for _ in range(int(round(seconds / STEP))):
            self.now += STEP
            self.tracker._track_objects(frame, list(hands), WIDTH, HEIGHT, now=self.now)

    def switch_on(self):
        """set_objects(True), with the settle time measured on this clock."""
        self.tracker.set_objects(True)
        self.tracker._recapture_at = self.now + OBJECT_SETTLE_SECONDS


@unittest.skipUnless(HAS_CV2, "needs OpenCV")
class ObjectStepTests(unittest.TestCase):
    """HandTracker._track_objects: the object sensor inside the camera loop."""

    def ready(self):
        """A tracker whose empty-desk photo has been taken."""
        tracker = make_tracker()
        clock = Clock(tracker)
        clock.switch_on()
        clock.run(desk(), OBJECT_SETTLE_SECONDS + 3.0)
        return tracker, clock

    def test_off_by_default_sends_nothing(self):
        tracker = make_tracker()
        Clock(tracker).run(with_box(desk(), 200, 150), 2.0)

        self.assertEqual(tracker.broadcaster.events, [])
        self.assertIsNone(tracker._detector)

    def test_switching_on_says_keep_the_desk_clear_then_tracks(self):
        tracker, _ = self.ready()
        self.assertEqual(tracker.broadcaster.states(), ["capturing", "tracking"])

    def test_nothing_is_photographed_during_the_settle_time(self):
        tracker = make_tracker()
        clock = Clock(tracker)
        clock.switch_on()

        # A hand still over the desk after pressing the button: not learnt.
        clock.run(with_box(desk(), 200, 150), OBJECT_SETTLE_SECONDS * 0.8)
        self.assertFalse(tracker._detector.background.capturing)
        self.assertFalse(tracker._detector.ready)

    def test_an_object_is_sent_in_desk_coordinates(self):
        tracker, clock = self.ready()
        clock.run(with_box(desk(), 280, 210), 1.0)

        item = tracker.broadcaster.of_type("objects")[-1]["objects"][0]
        self.assertEqual(item["id"], 1)
        self.assertAlmostEqual(item["x"], 0.5, delta=0.01)
        self.assertAlmostEqual(item["y"], 0.5, delta=0.01)

    def test_a_still_object_is_not_resent(self):
        tracker, clock = self.ready()
        clock.run(with_box(desk(), 280, 210), 1.0)
        sent = len(tracker.broadcaster.of_type("objects"))

        clock.run(with_box(desk(), 280, 210), 3.0)
        self.assertEqual(len(tracker.broadcaster.of_type("objects")), sent)

    def test_a_moving_object_is_resent_but_not_too_often(self):
        tracker, clock = self.ready()
        clock.run(with_box(desk(), 200, 210), 1.0)
        before = len(tracker.broadcaster.of_type("objects"))

        for step in range(30):        # two seconds of sliding
            clock.run(with_box(desk(), 200 + step * 4, 210), STEP)

        sent = len(tracker.broadcaster.of_type("objects")) - before
        self.assertGreater(sent, 5)
        self.assertLessEqual(sent, int(2.0 / OBJECT_SEND_SECONDS) + 1)

    def test_switching_off_clears_the_page(self):
        tracker, clock = self.ready()
        clock.run(with_box(desk(), 280, 210), 1.0)

        tracker.set_objects(False)
        clock.run(desk(), STEP)

        self.assertEqual(tracker.broadcaster.states()[-1], "off")
        self.assertEqual(tracker.broadcaster.of_type("objects")[-1]["objects"], [])
        self.assertIsNone(tracker._detector)

    def test_switching_on_again_starts_from_a_fresh_photo(self):
        # Button 3 on Desk: the mug that was there becomes part of "empty".
        tracker, clock = self.ready()
        mug = with_box(desk(), 280, 210)
        clock.run(mug, 1.0)

        clock.switch_on()
        clock.run(mug, OBJECT_SETTLE_SECONDS + 3.0)

        self.assertEqual(tracker.broadcaster.of_type("objects")[-1]["objects"], [])
        self.assertEqual(tracker.broadcaster.states()[-1], "tracking")

    def test_the_lights_going_out_is_reported(self):
        tracker, clock = self.ready()
        clock.run(np.full_like(desk(), 15), 1.0)

        self.assertEqual(tracker.broadcaster.states()[-1], "flooded")


class _FakeCapture:
    def __init__(self, tracker, frames):
        self.tracker = tracker
        self.frames = frames

    def read(self):
        if self.frames == 0:
            self.tracker.stop()
            return False, None
        self.frames -= 1
        return True, desk()


class _NoHands:
    def detect_for_video(self, image, stamp_ms):
        return type("Result", (), {"hand_landmarks": []})()


class _FakeMp:
    class ImageFormat:
        SRGB = 0

    @staticmethod
    def Image(image_format, data):
        return data


@unittest.skipUnless(HAS_CV2, "needs OpenCV")
class LoopTests(unittest.TestCase):
    """The real camera loop, with a painted camera and no MediaPipe."""

    def test_the_loop_feeds_the_object_sensor_when_desk_is_on(self):
        tracker = make_tracker()
        tracker.set_objects(True)
        tracker._recapture_at = 0.0          # no settle wait in a test

        saved = hand_tracker.mp
        hand_tracker.mp = _FakeMp
        try:
            tracker._loop(_FakeCapture(tracker, 80), _NoHands(), WIDTH, HEIGHT)
        finally:
            hand_tracker.mp = saved

        self.assertEqual(tracker.broadcaster.states(), ["capturing", "tracking"])


class FakeHands:
    """Stands in for the HandTracker the bridge hands to the server."""

    def __init__(self):
        self.calls = []

    def set_objects(self, on):
        self.calls.append(on)


class ObjectsRouteTests(unittest.TestCase):
    """POST /objects on a real HTTP server, as the page sends it."""

    def serve(self, hands):
        server = make_server(0, Broadcaster(), None, hands)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(),
                                 thread.join(timeout=5)))
        return "http://127.0.0.1:%d" % server.server_address[1]

    def post(self, url):
        request = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status

    def test_on_and_off_reach_the_tracker(self):
        hands = FakeHands()
        url = self.serve(hands)

        self.assertEqual(self.post(url + "/objects?on=1"), 200)
        self.assertEqual(self.post(url + "/objects?on=0"), 200)
        self.assertEqual(hands.calls, [True, False])

    def test_without_a_camera_it_is_503(self):
        url = self.serve(None)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(url + "/objects?on=1")
        self.assertEqual(caught.exception.code, 503)

    def test_other_posts_are_still_404(self):
        url = self.serve(FakeHands())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(url + "/nothing-here")
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
