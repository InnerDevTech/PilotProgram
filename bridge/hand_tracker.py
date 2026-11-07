"""Find a hand with the webcam and say where it is on the desk.

This is the Sprint 2 hand sensor. The original plan used 16 infrared beams
around a frame; this does the same job in software, with the webcam looking
down at the desk and Google's MediaPipe finding the hand in the picture.

What happens to one frame, in order:

    webcam frame  ->  MediaPipe finds 21 hand landmarks
                  ->  average three of them to get the palm centre, in pixels
                  ->  homography from calibration.json turns that into
                      desk coordinates, 0 to 1 across the projection
                  ->  Smoother takes the jitter out
                  ->  PresenceGate decides if the dot is allowed on screen
                  ->  {"type": "hand", "x": ..., "y": ...} to every browser tab

Run it on its own to see it working, with no bridge and no projector:

    python bridge/hand_tracker.py --preview        # camera window, with the dot
    python bridge/hand_tracker.py --print          # just print desk coordinates

The bridge runs it for real:

    python bridge/jarvis_bridge.py --camera 0

It needs calibration first - without calibration.json it has no idea which
part of the camera picture is the desk. Run bridge/calibrate_desk.py once.
"""

import argparse
import sys
import threading
import time
from pathlib import Path

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_tasks
    from mediapipe.tasks.python import vision as mp_vision
except ImportError:  # pragma: no cover
    mp = None

if __package__:
    from bridge import camera, desk_geometry
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from bridge import camera, desk_geometry


# ---------------------------------------------------------------- settings --

# The model MediaPipe needs. It is not on pip - see the README, or run this
# file with --where-is-the-model for the one command that fetches it.
MODEL_PATH = Path(__file__).resolve().parent / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")

# Landmarks 0, 5 and 17 are the wrist and the knuckles at each side of the
# palm. Averaging the three lands in the middle of the back of the hand, and
# - unlike a fingertip - it barely moves when fingers curl, which is what a
# steady dot needs.
PALM_LANDMARKS = (0, 5, 17)

# How sure MediaPipe has to be. Low values find hands in poor light but also
# find them in the woodgrain; these are the Sprint 2 "no false hands" knobs,
# together with PresenceGate.
MIN_DETECTION_CONFIDENCE = 0.5
MIN_PRESENCE_CONFIDENCE = 0.5
MIN_TRACKING_CONFIDENCE = 0.5

# Don't flood the browser: send a position only when it has actually moved
# this far across the desk (1/500th of the desk is well under a pixel).
MOVE_THRESHOLD = 0.002


# --------------------------------------------------------------- the model --

def model_is_missing():
    return not MODEL_PATH.is_file()


def model_help():
    """The message shown when the model file is not there yet."""
    return (
        f"MediaPipe's hand model is missing from {MODEL_PATH}.\n"
        f"Download it once (7.8 MB) with:\n\n"
        f"    python -c \"import urllib.request,pathlib;"
        f"p=pathlib.Path(r'{MODEL_PATH}');p.parent.mkdir(parents=True,exist_ok=True);"
        f"urllib.request.urlretrieve(r'{MODEL_URL}',p)\"\n"
    )


# -------------------------------------------------------------- the tracker --

class HandTracker:
    """Reads the webcam on a background thread and broadcasts hand positions.

    Built to the same shape as SerialLink in jarvis_bridge.py: construct it
    with the broadcaster, call start(), call stop() at the end. It never
    raises at the caller - problems become status events and printed lines,
    because a dead camera should not take the clock and timer down with it.
    """

    def __init__(self, broadcaster, camera_index=camera.DEFAULT_CAMERA, calibration_path=None,
                 smoothing=0.35, verbose=False):
        self.broadcaster = broadcaster
        self.camera_index = camera_index
        self.calibration_path = calibration_path or desk_geometry.DEFAULT_CALIBRATION_PATH
        self.verbose = verbose

        self.connected = False
        self.detail = "not started"

        self._matrix = None
        self._smoother = desk_geometry.Smoother(smoothing)
        self._gate = desk_geometry.PresenceGate()
        self._stop = threading.Event()
        self._last_sent = None
        # False, not None: an empty desk is the state the page already starts
        # in, so there is nothing to announce until a hand actually turns up.
        # (hand_status is what tells the page the sensor itself is alive.)
        self._last_present = False

    # ---- the bits the bridge calls ----

    def start(self):
        thread = threading.Thread(target=self._run, name="hand", daemon=True)
        thread.start()

    def stop(self):
        self._stop.set()

    def status_event(self):
        return {"type": "hand_status", "connected": self.connected,
                "detail": self.detail}

    # ---- turning one frame into one message ----

    def frame_to_desk(self, landmarks, frame_width, frame_height):
        """Palm landmarks -> desk coordinates. Pure, so the tests can call it.

        `landmarks` is MediaPipe's list for one hand: each has .x and .y as a
        fraction of the frame, which is why the frame size has to come in too.
        """
        if self._matrix is None:
            raise ValueError("not calibrated")

        xs = [landmarks[i].x * frame_width for i in PALM_LANDMARKS]
        ys = [landmarks[i].y * frame_height for i in PALM_LANDMARKS]
        pixel = (sum(xs) / len(xs), sum(ys) / len(ys))

        return desk_geometry.apply_homography(self._matrix, pixel)

    def _publish(self, point):
        """Send a position, but only when it is worth sending."""
        if point is None:
            if self._last_present is not False:
                self.broadcaster.send({"type": "hand", "present": False})
                self._last_present = False
                self._last_sent = None
            return

        x, y = point
        if self._last_sent is not None:
            dx = abs(x - self._last_sent[0])
            dy = abs(y - self._last_sent[1])
            if dx < MOVE_THRESHOLD and dy < MOVE_THRESHOLD:
                return

        self.broadcaster.send({"type": "hand", "x": round(x, 4), "y": round(y, 4)})
        self._last_sent = (x, y)
        self._last_present = True

    def _fail(self, detail):
        self.connected = False
        self.detail = detail
        print(detail)
        self.broadcaster.send(self.status_event())
        return 1

    # ---- the loop ----

    def _run(self):
        if cv2 is None or mp is None:
            return self._fail("OpenCV or MediaPipe is not installed. "
                              "Run: python -m pip install -r requirements.txt")

        if model_is_missing():
            return self._fail(model_help())

        try:
            self._matrix, _ = desk_geometry.load_calibration(path=self.calibration_path)
        except FileNotFoundError:
            return self._fail("The desk is not calibrated yet. "
                              "Run: python bridge/calibrate_desk.py")
        except ValueError as err:
            return self._fail(str(err))

        try:
            capture, width, height = _open_camera(self.camera_index)
        except LookupError as err:
            return self._fail(str(err))
        if capture is None:
            return self._fail(f"Cannot open {camera.describe(self.camera_index)}. "
                              "Is another program using it?")

        landmarker = _make_landmarker()

        self.connected = True
        self.detail = f"{camera.describe(self.camera_index)} at {width}x{height}"
        print(f"Hand sensor: {self.detail}")
        self.broadcaster.send(self.status_event())

        try:
            self._loop(capture, landmarker, width, height)
        finally:
            capture.release()
            landmarker.close()
            self.connected = False
            self.detail = "stopped"
            self.broadcaster.send(self.status_event())

    def _loop(self, capture, landmarker, width, height):
        started = time.monotonic()

        while not self._stop.is_set():
            ok, frame = capture.read()
            if not ok:
                time.sleep(0.05)
                continue

            # MediaPipe wants RGB; OpenCV hands out BGR.
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            # VIDEO mode needs a timestamp that only ever goes up.
            stamp_ms = int((time.monotonic() - started) * 1000)
            result = landmarker.detect_for_video(image, stamp_ms)

            point = None
            if result.hand_landmarks:
                try:
                    point = self.frame_to_desk(result.hand_landmarks[0], width, height)
                except ValueError:
                    point = None

            # A hand held beside the desk is a real hand, but it is not on the
            # desk, so it must not put a dot on the projection.
            found = point is not None and desk_geometry.on_desk(point, margin=0.05)

            if self._gate.update(found):
                if not found:
                    # The gate is riding out a few missed frames so the dot
                    # does not blink. There is no new position to smooth, so
                    # leave the dot where it was last seen.
                    continue
                smoothed = self._smoother.update(point)
                self._publish(smoothed)
                if self.verbose:
                    print(f"hand at ({smoothed[0]:.3f}, {smoothed[1]:.3f})")
            else:
                self._smoother.reset()
                self._publish(None)


# --------------------------------------------------------------- plumbing --

def _open_camera(choice):
    """Open a webcam by name or number, preferring the quick Windows backend.

    Raises LookupError if a name does not match any camera.
    """
    index = camera.resolve(choice)
    capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not capture.isOpened():
        capture = cv2.VideoCapture(index)
    if not capture.isOpened():
        return None, 0, 0

    return (capture,
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))


def _make_landmarker():
    options = mp_vision.HandLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=MIN_DETECTION_CONFIDENCE,
        min_hand_presence_confidence=MIN_PRESENCE_CONFIDENCE,
        min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
    )
    return mp_vision.HandLandmarker.create_from_options(options)


class PrintBroadcaster:
    """Stands in for the bridge's Broadcaster when running this file alone."""

    def __init__(self, quiet=False):
        self.quiet = quiet
        self.last = None

    def send(self, event):
        self.last = event
        if not self.quiet:
            print(event)


# ------------------------------------------------------------------- main --

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", default=camera.DEFAULT_CAMERA,
                        help=f"camera name or number (default {camera.DEFAULT_CAMERA})")
    parser.add_argument("--preview", action="store_true",
                        help="show the camera window with the hand marked")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print desk coordinates, no window")
    parser.add_argument("--smoothing", type=float, default=0.35,
                        help="0.05 very smooth, 1.0 none (default 0.35)")
    parser.add_argument("--where-is-the-model", action="store_true",
                        help="print how to fetch the MediaPipe model and exit")
    args = parser.parse_args(argv)

    if args.where_is_the_model:
        print(model_help())
        return 0

    if cv2 is None or mp is None:
        print("OpenCV or MediaPipe is not installed. "
              "Run: python -m pip install -r requirements.txt")
        return 1

    if model_is_missing():
        print(model_help())
        return 1

    if args.preview:
        return preview(args.camera, args.smoothing)

    tracker = HandTracker(PrintBroadcaster(), camera_index=args.camera,
                          smoothing=args.smoothing, verbose=False)
    tracker.start()
    print("Tracking. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        tracker.stop()
        return 0


def preview(camera_index, smoothing):
    """A camera window with the palm point drawn on it, for tuning.

    This is the quickest way to answer "is it even seeing my hand?" without
    the projector, the bridge or the browser being involved.
    """
    try:
        matrix, _ = desk_geometry.load_calibration()
    except (FileNotFoundError, ValueError) as err:
        print(err if isinstance(err, ValueError) else
              "Not calibrated. Run: python bridge/calibrate_desk.py")
        return 1

    try:
        capture, width, height = _open_camera(camera_index)
    except LookupError as err:
        print(err)
        return 1
    if capture is None:
        print(f"Cannot open {camera.describe(camera_index)}.")
        return 1

    landmarker = _make_landmarker()
    smoother = desk_geometry.Smoother(smoothing)
    gate = desk_geometry.PresenceGate()
    started = time.monotonic()

    print("Preview running. q to quit.")

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect_for_video(
                image, int((time.monotonic() - started) * 1000))

            label = "no hand"
            if result.hand_landmarks:
                marks = result.hand_landmarks[0]
                xs = [marks[i].x * width for i in PALM_LANDMARKS]
                ys = [marks[i].y * height for i in PALM_LANDMARKS]
                pixel = (sum(xs) / len(xs), sum(ys) / len(ys))

                desk = desk_geometry.apply_homography(matrix, pixel)
                inside = desk_geometry.on_desk(desk, margin=0.05)

                if gate.update(inside):
                    desk = smoother.update(desk)
                    label = f"desk ({desk[0]:+.3f}, {desk[1]:+.3f})"
                else:
                    smoother.reset()
                    label = "hand, but off the desk"

                colour = (120, 240, 120) if inside else (60, 190, 250)
                cv2.circle(frame, (int(pixel[0]), int(pixel[1])), 10, colour, -1)
            else:
                gate.update(False)
                smoother.reset()

            cv2.rectangle(frame, (0, 0), (frame.shape[1], 34), (0, 0, 0), -1)
            cv2.putText(frame, label, (12, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (255, 255, 255), 2)
            cv2.imshow("Jarvis Desk hand preview", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        capture.release()
        landmarker.close()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
