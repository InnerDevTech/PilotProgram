"""Find the things sitting on the desk, with no markers stuck to them.

This is the Sprint 3 object sensor. The original plan put a printed marker on
every object; this does it by background subtraction instead, so anything you
put down is found - a mug, a phone, a roll of tape - and it gets its real
outline, not a ring.

What happens to one frame, in order:

    webcam frame  ->  compare it with a picture of the EMPTY desk
                  ->  whatever is different is "foreground"
                  ->  cut the hand out (MediaPipe already knows where it is)
                  ->  each blob that is left is a possible object
                  ->  a blob that sits still for half a second becomes a real
                      object with a number, and keeps that number as it moves

Run it on its own, camera only - no bridge, no projector:

    python bridge/object_tracker.py --preview      # camera window with outlines
    python bridge/object_tracker.py --print        # just print what comes and goes

It photographs the empty desk when it starts, so start it with the desk clear.
Press c in the preview window to photograph the empty desk again.

It is NOT wired into the bridge yet. That is the next step, once the outlines
are right in the preview: the bridge will hand each frame to ObjectDetector
and send objects_event() to the page, the same way the hand dot travels.
"""

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

if __package__:
    from bridge import camera, desk_geometry
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from bridge import camera, desk_geometry


# ---------------------------------------------------------------- settings --

# Every distance below is in camera pixels for a 640-pixel-wide picture.
# ObjectDetector scales them if the camera runs at another size.
REFERENCE_WIDTH = 640

# How different a pixel has to be from the empty desk (0 to 255) to count.
# Too low and shadows become objects; too high and a pale object on a pale
# desk is missed. This is the first knob to turn.
DIFF_THRESHOLD = 28

# SHADOWS. A shadow is not an object, but it does change the picture. What
# gives a shadow away is that it only DIMS the desk: every pixel gets darker
# by about the same fraction, the colour stays the same, and the grain of the
# surface (carpet speckle, wood, scratches) is all still there underneath.
# An object replaces the surface with something else.
#
# So a changed pixel is forgiven as "just the light" when all three hold:
#   * its brightness is between SHADOW_DARKEST and HIGHLIGHT_BRIGHTEST times
#     what the empty desk was (0.45 = a shadow may take away up to 55%),
#   * its colour mix has moved by less than COLOUR_TOLERANCE, and
#   * its texture has changed by less than TEXTURE_TOLERANCE.
# The same test forgives the webcam's auto-exposure nudging the whole picture
# a little brighter or darker when a hand comes into view.
SHADOW_DARKEST = 0.45
HIGHLIGHT_BRIGHTEST = 1.25
COLOUR_TOLERANCE = 0.035
TEXTURE_TOLERANCE = 0.05

# Texture is measured as contrast (how much pixels vary, divided by how bright
# they are) inside a square this many pixels across.
TEXTURE_WINDOW = 11

# Blobs smaller than this many pixels are ignored: crumbs, noise, a pen cap.
# 900 is a 30 x 30 pixel square.
MIN_AREA = 900

# Blobs thinner than this are ignored too. The edge of a sharp shadow can
# show up as a long thin strip; a real object is thicker than that. (The
# thickness is worked out as 2 x area / outline length, which is the width
# for a long strip and about the diameter for a round thing.)
MIN_THICKNESS = 14

# How far past the hand's outline to cut. Bigger hides more of the wrist and
# the hand's shadow, but also hides more of an object the hand is touching.
HAND_PADDING = 22

# If this much of the picture is "different", it is not an object - the
# lights changed or the camera moved. Time to photograph the desk again.
FLOOD_FRACTION = 0.5

# A blob must sit still (within STILL_DISTANCE) for this long to become an
# object. This is what stops a sleeve passing over the desk being one.
APPEAR_SECONDS = 0.5
STILL_DISTANCE = 6

# An object that cannot be seen is kept for this long before it is dropped,
# so a few bad frames do not blink its outline off.
VANISH_SECONDS = 1.0

# A blob this close to where an object was last frame IS that object.
MAX_JUMP = 90

# Picking an object up and putting it down somewhere else makes it vanish
# and reappear, which on its own would give it a new number. So an object
# that vanished is remembered for this long, and a new object that looks
# like it - about the same size and colour - gets its old number back.
REMEMBER_SECONDS = 15
SAME_SIZE = 0.35          # sizes within 35% of each other count as the same
SAME_COLOUR = 40          # average colours this close (0-255 per channel) too

# The empty-desk picture: how many frames to average, and how quickly it
# drifts to follow slow changes in the room light (0 = never, 1 = instantly).
# Only pixels within LEARN_ONLY_WITHIN of the remembered brightness are
# learnt: slow light changes are, a shadow parked on the desk is not.
BACKGROUND_FRAMES = 30
LEARN_RATE = 0.02
LEARN_ONLY_WITHIN = 0.08

# Let the webcam's auto-exposure settle before trusting what it shows.
WARMUP_FRAMES = 30


# ------------------------------------------------------------- empty desk --

def _soften(frame):
    """Blur a frame slightly, so single noisy pixels do not count as change."""
    return cv2.GaussianBlur(frame, (5, 5), 0).astype(np.float32)


def _grey(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _contrast(grey):
    """How grainy the picture is around each pixel.

    It is the spread of brightness in a small window divided by the average
    brightness there. Dividing is the point: a shadow makes the spread AND the
    average smaller by the same fraction, so the answer does not change. The
    +16 stops very dark patches, where it is all camera noise, blowing up.
    """
    window = (TEXTURE_WINDOW, TEXTURE_WINDOW)
    mean = cv2.blur(grey, window)
    mean_of_squares = cv2.blur(grey * grey, window)
    spread = np.sqrt(np.maximum(mean_of_squares - mean * mean, 0))
    return spread / (mean + 16)


class BackgroundModel:
    """Remembers what the empty desk looks like.

    capture: call start_capture(), then feed() frames until it returns True.
    The frames are averaged, which takes the camera noise out.

    After that, changed() says which pixels of a new frame are not the empty
    desk any more (and are not just a shadow), and learn() nudges the memory
    towards the new frame so that the sun moving round the room does not
    slowly turn into objects.
    """

    def __init__(self, capture_frames=BACKGROUND_FRAMES, learn_rate=LEARN_RATE):
        self.capture_frames = capture_frames
        self.learn_rate = learn_rate
        self._background = None     # the empty desk, softened, in colour
        self._sharp = None          # the empty desk, NOT softened, in grey
        self._texture = None        # _contrast() of the empty desk
        self._sums = None
        self._count = 0
        self.capturing = False
        # From the last changed(): white where brightness is near what it
        # was, so it is safe to learn there. A shadow is not.
        self.steady = None

    @property
    def ready(self):
        return self._background is not None and not self.capturing

    def start_capture(self):
        self._sums = None
        self._count = 0
        self.capturing = True

    def feed(self, frame):
        """Add one frame of the empty desk. True once enough have arrived."""
        soft, sharp = _soften(frame), _grey(frame)
        if self._sums is None:
            self._sums = [soft, sharp]
        else:
            self._sums[0] += soft
            self._sums[1] += sharp
        self._count += 1

        if self._count < self.capture_frames:
            return False

        self._background = self._sums[0] / self._count
        self._sharp = self._sums[1] / self._count
        self._texture = _contrast(self._sharp)
        self._sums = None
        self.capturing = False
        return True

    def difference(self, frame):
        """How different each pixel is from the empty desk, 0 to 255.

        The biggest of the blue, green and red differences is used, so a red
        mug on a grey desk of the same brightness still shows up.
        """
        gap = cv2.absdiff(_soften(frame), self._background)
        return gap.max(axis=2).astype(np.uint8)

    def texture_change(self, frame):
        """How much the grain of the surface has changed at each pixel."""
        return cv2.absdiff(_contrast(_grey(frame)), self._texture)

    def changed(self, frame, threshold=DIFF_THRESHOLD):
        """A mask, white where the frame is no longer the empty desk.

        Two ways for a pixel to count:
          * its texture changed - smooth plastic where there was carpet. This
            is what finds a dark object on a dark desk;
          * its colour or brightness changed by more than `threshold`, and it
            does not pass the "just the light" test described in the settings.
        """
        soft = _soften(frame)
        texture_changed = self.texture_change(frame) > TEXTURE_TOLERANCE

        gap = cv2.absdiff(soft, self._background).max(axis=2)

        # Brightness as a fraction of what the empty desk was.
        now_total = soft.sum(axis=2) + 1.0
        then_total = self._background.sum(axis=2) + 1.0
        ratio = now_total / then_total
        dimmed = (ratio > SHADOW_DARKEST) & (ratio < HIGHLIGHT_BRIGHTEST)
        steady = np.abs(ratio - 1.0) < LEARN_ONLY_WITHIN
        self.steady = steady.astype(np.uint8) * 255

        # Colour mix: each channel's share of the total. A shadow leaves the
        # shares alone; a red mug does not.
        shares_now = soft / now_total[..., None]
        shares_then = self._background / then_total[..., None]
        same_colour = np.abs(shares_now - shares_then).max(axis=2) < COLOUR_TOLERANCE

        just_the_light = dimmed & same_colour & ~texture_changed
        different = texture_changed | ((gap > threshold) & ~just_the_light)
        return different.astype(np.uint8) * 255

    def learn(self, frame, allowed):
        """Drift towards this frame, but only where `allowed` is non-zero.

        Objects and hands must be kept out of `allowed`, or a mug left on the
        desk would slowly be learnt as "empty desk" and its outline would fade.
        """
        if self.learn_rate > 0:
            cv2.accumulateWeighted(_soften(frame), self._background,
                                   self.learn_rate, mask=allowed)
            cv2.accumulateWeighted(_grey(frame), self._sharp,
                                   self.learn_rate, mask=allowed)
            self._texture = _contrast(self._sharp)


# ------------------------------------------------------------------ blobs --

class Blob:
    """One patch of foreground in one frame: where it is and its outline.

    `colour` is its average blue, green and red, or None when not known.
    """

    def __init__(self, centre, area, contour, colour=None):
        self.centre = (float(centre[0]), float(centre[1]))
        self.area = float(area)
        self.contour = contour          # N x 2 array of camera pixels
        self.colour = colour


def clean_mask(mask):
    """Tidy a raw changed-pixels mask into solid shapes.

    "Open" removes specks smaller than the brush; "close" fills the holes and
    gaps inside an object, so a mug is one blob and not a rim and a handle.
    """
    small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    large = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, small)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, large)


def hand_mask(shape, hands, padding=HAND_PADDING):
    """A mask that is white over every hand, plus `padding` pixels all round.

    `hands` is a list of hands; each hand is a list of (x, y) camera pixels,
    one per MediaPipe landmark.
    """
    mask = np.zeros(shape[:2], dtype=np.uint8)
    for points in hands:
        hull = cv2.convexHull(np.array(points, dtype=np.int32))
        cv2.fillConvexPoly(mask, hull, 255)

    if hands and padding > 0:
        size = 2 * int(padding) + 1
        brush = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        mask = cv2.dilate(mask, brush)
    return mask


def find_blobs(mask, min_area=MIN_AREA, min_thickness=MIN_THICKNESS, frame=None):
    """Every blob in the mask that could be an object.

    Two kinds are thrown away: blobs that are too small, and blobs that touch
    the edge of the picture. An arm always comes in from the edge, so that one
    rule gets rid of arms even on frames where MediaPipe missed the hand.
    (The cost: an object poking out of the camera's view is ignored too.)
    Too-thin blobs go as well - see MIN_THICKNESS.

    With `frame` given, each blob also gets its average colour.
    """
    height, width = mask.shape[:2]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    blobs = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        if x <= 0 or y <= 0 or x + w >= width or y + h >= height:
            continue

        if 2 * area / max(cv2.arcLength(contour, True), 1) < min_thickness:
            continue

        colour = None
        if frame is not None:
            inside = np.zeros(mask.shape[:2], dtype=np.uint8)
            cv2.drawContours(inside, [contour], -1, 255, -1)
            colour = tuple(float(c) for c in cv2.mean(frame, mask=inside)[:3])

        moments = cv2.moments(contour)
        centre = (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])

        # Drop the points that add nothing: fewer numbers to send to the page.
        outline = cv2.approxPolyDP(contour, 2.0, True).reshape(-1, 2)
        blobs.append(Blob(centre, area, outline, colour))

    return blobs


# ---------------------------------------------------------------- tracking --

class TrackedObject:
    """One thing on the desk, followed from frame to frame."""

    def __init__(self, blob, now):
        self.id = None                  # given a number once it is confirmed
        self.confirmed = False
        self.centre = blob.centre
        self.area = blob.area
        self.contour = blob.contour
        self.colour = blob.colour
        self.last_seen = now
        self._still_since = now
        self._still_at = blob.centre


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class ObjectTracker:
    """Turns each frame's blobs into steady, numbered objects.

    Pure logic - no camera, no OpenCV - so the tests can drive it with made-up
    blobs and a made-up clock. Four rules:

      * a new blob must sit still for `appear_seconds` before it counts;
      * a blob near where an object was last frame is that same object, so a
        mug keeps its number while it is slid about;
      * an object that goes missing is kept for `vanish_seconds` - and for as
        long as a hand is over it, because a hand covering a mug has not
        made the mug go away;
      * an object that does go is remembered for `remember_seconds`, and a
        new object that looks like it (same size and colour) gets its old
        number back - that is a mug picked up and put down somewhere else.
    """

    def __init__(self, appear_seconds=APPEAR_SECONDS, vanish_seconds=VANISH_SECONDS,
                 max_jump=MAX_JUMP, still_distance=STILL_DISTANCE,
                 remember_seconds=REMEMBER_SECONDS):
        self.appear_seconds = appear_seconds
        self.vanish_seconds = vanish_seconds
        self.max_jump = max_jump
        self.still_distance = still_distance
        self.remember_seconds = remember_seconds
        self._objects = []
        self._gone = []           # (time it went, the object), newest last
        self._next_id = 1

    def reset(self):
        """Forget everything (the empty desk was photographed again)."""
        self._objects = []
        self._gone = []

    def update(self, blobs, now, hands=None):
        """Feed in this frame's blobs. Returns the confirmed objects.

        `hands` is the hand mask for this frame, or None for "no hands".
        """
        # Pair each object with its nearest blob, closest pairs first.
        pairs = sorted(
            (_distance(obj.centre, blob.centre), o, b)
            for o, obj in enumerate(self._objects)
            for b, blob in enumerate(blobs))

        matched_objects = set()
        matched_blobs = set()
        for gap, o, b in pairs:
            if gap > self.max_jump:
                break
            if o in matched_objects or b in matched_blobs:
                continue
            matched_objects.add(o)
            matched_blobs.add(b)
            self._see(self._objects[o], blobs[b], now)

        kept = []
        for o, obj in enumerate(self._objects):
            if o not in matched_objects:
                if obj.confirmed and _covered(obj, hands):
                    obj.last_seen = now       # under a hand: hold it
                # A blob that never became an object gets no second chances.
                patience = self.vanish_seconds if obj.confirmed else 0.0
                if now - obj.last_seen > patience:
                    if obj.confirmed:
                        self._gone.append((now, obj))
                    continue
            kept.append(obj)

        self._gone = [(when, obj) for when, obj in self._gone
                      if now - when <= self.remember_seconds]

        for b, blob in enumerate(blobs):
            if b not in matched_blobs:
                kept.append(TrackedObject(blob, now))

        self._objects = kept
        return self.objects()

    def objects(self):
        return [obj for obj in self._objects if obj.confirmed]

    def _see(self, obj, blob, now):
        obj.centre = blob.centre
        obj.area = blob.area
        obj.contour = blob.contour
        obj.colour = blob.colour
        obj.last_seen = now

        if obj.confirmed:
            return

        if _distance(blob.centre, obj._still_at) > self.still_distance:
            # Still moving: start the stillness clock again from here.
            obj._still_at = blob.centre
            obj._still_since = now
        elif now - obj._still_since >= self.appear_seconds:
            obj.confirmed = True
            obj.id = self._number_for(obj)

    def _number_for(self, obj):
        """An old number if this looks like something that just went, else a new one."""
        in_use = {other.id for other in self._objects if other.confirmed}
        best = None
        for index, (_, gone) in enumerate(self._gone):
            if gone.id in in_use or not _looks_alike(obj, gone):
                continue
            gap = _distance(obj.centre, gone.centre)
            if best is None or gap < best[0]:
                best = (gap, index)

        if best is not None:
            return self._gone.pop(best[1])[1].id

        number = self._next_id
        self._next_id += 1
        return number


def _looks_alike(a, b):
    """About the same size, and - when both colours are known - the same colour."""
    small, large = sorted((a.area, b.area))
    if large <= 0 or small / large < 1 - SAME_SIZE:
        return False
    if a.colour is not None and b.colour is not None:
        if max(abs(x - y) for x, y in zip(a.colour, b.colour)) > SAME_COLOUR:
            return False
    return True


def _covered(obj, hands):
    """True when a hand is over any part of the object's last known outline."""
    if hands is None:
        return False
    height, width = hands.shape[:2]
    points = np.vstack([obj.contour, [obj.centre]]).astype(int)
    xs = np.clip(points[:, 0], 0, width - 1)
    ys = np.clip(points[:, 1], 0, height - 1)
    return bool(hands[ys, xs].any())


# ------------------------------------------------------------ the detector --

class ObjectDetector:
    """The whole pipeline for one camera: frame in, list of objects out.

    It does not own the camera. Whoever does (the preview below, and later
    the bridge's camera loop) calls process() once per frame:

        detector = ObjectDetector((width, height), matrix)
        detector.recapture()                       # desk must be empty
        objects = detector.process(frame, hands)   # every frame

    `matrix` is the homography from calibration.json. With it, only blobs on
    the projection count. Without it (None) the whole picture is the desk.
    """

    def __init__(self, frame_size, matrix=None):
        self.frame_size = (int(frame_size[0]), int(frame_size[1]))
        self.matrix = matrix

        scale = self.frame_size[0] / REFERENCE_WIDTH
        self.min_area = MIN_AREA * scale * scale
        self.min_thickness = MIN_THICKNESS * scale
        self.hand_padding = HAND_PADDING * scale
        self.background = BackgroundModel()
        self.tracker = ObjectTracker(max_jump=MAX_JUMP * scale,
                                     still_distance=STILL_DISTANCE * scale)

        # Left over from the last frame, for the preview window to draw.
        self.mask = None
        self.flooded = False

    @property
    def ready(self):
        return self.background.ready

    def recapture(self):
        """Photograph the empty desk again, over the next few frames."""
        self.background.start_capture()
        self.tracker.reset()
        self.flooded = False

    def process(self, frame, hands=(), now=None):
        """One frame in, the confirmed objects out.

        `hands` is a list of hands, each a list of (x, y) camera pixels.
        """
        if now is None:
            now = time.perf_counter()

        if self.background.capturing:
            self.background.feed(frame)
            return []
        if not self.background.ready:
            return []

        mask = clean_mask(self.background.changed(frame))
        hand_area = hand_mask(frame.shape, hands, self.hand_padding)
        mask[hand_area > 0] = 0
        self.mask = mask

        self.flooded = cv2.countNonZero(mask) > FLOOD_FRACTION * mask.size
        if self.flooded:
            return []

        blobs = [blob for blob in find_blobs(mask, self.min_area,
                                             self.min_thickness, frame)
                 if self._on_desk(blob.centre)]
        objects = self.tracker.update(blobs, now, hand_area if hands else None)

        # Learn the slow changes, keeping well clear of anything that is
        # there - and of shadows, which are not part of the empty desk.
        brush = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        busy = cv2.bitwise_or(cv2.dilate(mask, brush), hand_area)
        allowed = cv2.bitwise_and(cv2.bitwise_not(busy), self.background.steady)
        self.background.learn(frame, allowed)

        return objects

    def _on_desk(self, pixel):
        if self.matrix is None:
            return True
        try:
            point = desk_geometry.apply_homography(self.matrix, pixel)
        except ValueError:
            return False
        return desk_geometry.on_desk(point, margin=0.02)


# ------------------------------------------------------- what the page gets --

def objects_event(objects, frame_size, matrix=None):
    """The message for the browser: every object, in desk coordinates.

        {"type": "objects", "objects": [
            {"id": 1, "x": 0.42, "y": 0.61, "outline": [[0.40, 0.58], ...]}]}

    Desk coordinates are 0 to 1 across the projection, the same as the hand
    dot. Without calibration (matrix None) they are 0 to 1 across the camera
    picture instead, which is enough to test with.
    """
    width, height = frame_size

    def to_desk(pixel):
        if matrix is None:
            return pixel[0] / width, pixel[1] / height
        return desk_geometry.apply_homography(matrix, pixel)

    listed = []
    for obj in objects:
        x, y = to_desk(obj.centre)
        outline = [[round(px, 4), round(py, 4)]
                   for px, py in (to_desk(point) for point in obj.contour)]
        listed.append({"id": obj.id, "x": round(x, 4), "y": round(y, 4),
                       "outline": outline})

    return {"type": "objects", "objects": listed}


# ------------------------------------------------------------------- main --

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", default=camera.DEFAULT_CAMERA,
                        help=f"camera name or number (default {camera.DEFAULT_CAMERA})")
    parser.add_argument("--preview", action="store_true",
                        help="show the camera window with the objects outlined")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print objects as they come and go, no window")
    parser.add_argument("--no-hands", action="store_true",
                        help="do not look for hands (quicker to start; your "
                             "arm is still ignored, your hand may not be)")
    args = parser.parse_args(argv)

    if cv2 is None:
        print("OpenCV is not installed. "
              "Run: python -m pip install -r requirements.txt")
        return 1

    return run(args.camera, window=not args.print_only, use_hands=not args.no_hands)


def _load_hands(use_hands):
    """MediaPipe's hand finder, or None with a printed reason.

    Imported here and not at the top: it takes seconds, and the detector
    itself (and its tests) never needs it.
    """
    if not use_hands:
        return None, None

    from bridge import hand_tracker
    if hand_tracker.mp is None:
        print("MediaPipe is not installed - carrying on without hand masking.")
        return None, None
    if hand_tracker.model_is_missing():
        print(hand_tracker.model_help())
        print("Carrying on without hand masking.")
        return None, None

    options = hand_tracker.mp_vision.HandLandmarkerOptions(
        base_options=hand_tracker.mp_tasks.BaseOptions(
            model_asset_path=str(hand_tracker.MODEL_PATH)),
        running_mode=hand_tracker.mp_vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=hand_tracker.MIN_DETECTION_CONFIDENCE,
        min_hand_presence_confidence=hand_tracker.MIN_PRESENCE_CONFIDENCE,
        min_tracking_confidence=hand_tracker.MIN_TRACKING_CONFIDENCE,
    )
    return hand_tracker.mp, hand_tracker.mp_vision.HandLandmarker.create_from_options(options)


def _open_camera(choice):
    index = camera.resolve(choice)
    capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not capture.isOpened():
        capture = cv2.VideoCapture(index)
    if not capture.isOpened():
        return None, 0, 0
    return (capture,
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))


def run(camera_choice, window=True, use_hands=True):
    """The camera loop for --preview and --print."""
    try:
        matrix, _ = desk_geometry.load_calibration()
    except (FileNotFoundError, ValueError):
        matrix = None
        print("No calibration - treating the whole camera picture as the desk.")

    try:
        capture, width, height = _open_camera(camera_choice)
    except LookupError as err:
        print(err)
        return 1
    if capture is None:
        print(f"Cannot open {camera.describe(camera_choice)}. "
              "Is another program (the bridge?) using it?")
        return 1

    mp, landmarker = _load_hands(use_hands)
    detector = ObjectDetector((width, height), matrix)
    desk_outline = _desk_outline(matrix)

    print("Keep the desk CLEAR for a moment - photographing it empty...")
    if window:
        print("Keys: c = photograph the empty desk again, m = show the mask, q = quit.")
    else:
        print("Ctrl+C to stop.")

    started = time.perf_counter()
    last_stamp = -1
    warmup = WARMUP_FRAMES
    show_mask = False
    known = {}

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                time.sleep(0.05)
                continue

            # The first frames are the webcam still choosing its exposure.
            if warmup > 0:
                warmup -= 1
                if warmup == 0:
                    detector.recapture()
                continue

            hands = []
            if landmarker is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                stamp_ms = max(int((time.perf_counter() - started) * 1000),
                               last_stamp + 1)
                last_stamp = stamp_ms
                result = landmarker.detect_for_video(image, stamp_ms)
                hands = [[(mark.x * width, mark.y * height) for mark in hand]
                         for hand in result.hand_landmarks]

            was_capturing = detector.background.capturing
            objects = detector.process(frame, hands)
            if was_capturing and detector.ready:
                print("Empty desk photographed. Put something down.")

            known = _report(objects, known, detector, matrix)

            if window:
                picture = _draw(frame, detector, objects, hands, desk_outline, show_mask)
                cv2.imshow("Jarvis Desk object preview", picture)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("c"):
                    print("Keep the desk CLEAR - photographing it empty...")
                    detector.recapture()
                    known = {}
                if key == ord("m"):
                    show_mask = not show_mask
    except KeyboardInterrupt:
        pass
    finally:
        capture.release()
        if landmarker is not None:
            landmarker.close()
        if window:
            cv2.destroyAllWindows()

    return 0


def _report(objects, known, detector, matrix):
    """Print one line when an object appears or goes. Returns the new list."""
    event = objects_event(objects, detector.frame_size, matrix)
    now = {item["id"]: item for item in event["objects"]}

    for number, item in now.items():
        if number not in known:
            print(f"object {number} appeared at ({item['x']:.3f}, {item['y']:.3f})")
    for number in known:
        if number not in now:
            print(f"object {number} gone")
    return now


def _desk_outline(matrix):
    """The projection's four corners in camera pixels, for drawing."""
    if matrix is None:
        return None
    back = np.linalg.inv(matrix)
    corners = [desk_geometry.apply_homography(back, corner)
               for corner in desk_geometry.DESK_CORNERS]
    return np.array(corners, dtype=np.int32)


def _draw(frame, detector, objects, hands, desk_outline, show_mask):
    if show_mask and detector.mask is not None:
        picture = cv2.cvtColor(detector.mask, cv2.COLOR_GRAY2BGR)
    else:
        picture = frame.copy()

    if desk_outline is not None:
        cv2.polylines(picture, [desk_outline], True, (255, 255, 255), 1)

    for points in hands:
        hull = cv2.convexHull(np.array(points, dtype=np.int32))
        cv2.polylines(picture, [hull], True, (60, 190, 250), 2)

    for obj in objects:
        cv2.polylines(picture, [obj.contour.astype(np.int32)], True, (120, 240, 120), 2)
        spot = (int(obj.centre[0]), int(obj.centre[1]))
        cv2.circle(picture, spot, 4, (120, 240, 120), -1)
        cv2.putText(picture, f"#{obj.id}", (spot[0] + 8, spot[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 240, 120), 2)

    if detector.background.capturing or not detector.ready:
        label = "photographing the empty desk - keep it clear"
    elif detector.flooded:
        label = "everything changed - clear the desk and press c"
    else:
        label = f"{len(objects)} object" + ("" if len(objects) == 1 else "s")

    cv2.rectangle(picture, (0, 0), (picture.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(picture, label, (12, 24), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (255, 255, 255), 2)
    return picture


if __name__ == "__main__":
    raise SystemExit(main())
