"""Turning camera pixels into desk coordinates.

The webcam looks down at the desk from above, at an angle, so the projection
rectangle is never a neat rectangle in the camera picture - it is a wonky
four-sided shape. This file holds the maths that straightens it out, and
nothing else: no camera, no MediaPipe, no threads. That is on purpose, so
tests/test_desk_geometry.py can check every line of it with no hardware
plugged in.

DESK COORDINATES. Everywhere in Jarvis Desk, a position on the desk is two
numbers from 0 to 1:

    (0,0) ------------- (1,0)      x runs left to right
      |                   |        y runs top to bottom
      |   the projection  |        (0.5, 0.5) is the middle
      |                   |
    (0,1) ------------- (1,1)

The projector draws the page across exactly that rectangle, so a hand at
(0.5, 0.5) in desk coordinates must have its dot drawn in the middle of the
page. Getting from camera pixels to those two numbers is what a homography
does, and finding the homography is what calibration is for.
"""

import json
from pathlib import Path

import numpy as np

# Where calibrate_desk.py writes its answer, and the bridge reads it from.
DEFAULT_CALIBRATION_PATH = Path(__file__).resolve().parent.parent / "calibration.json"

# The four desk corners, in the order the calibration tool asks you to click
# them. Clockwise from the top left.
CORNER_NAMES = ("top left", "top right", "bottom right", "bottom left")
DESK_CORNERS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


# ------------------------------------------------------------- homography --

def corners_to_homography(corners):
    """Build the 3x3 matrix that maps camera pixels to desk coordinates.

    `corners` is the four corners of the projection as seen by the camera, in
    pixels, in CORNER_NAMES order. The matrix it returns sends those four
    points to (0,0), (1,0), (1,1) and (0,1) - and, because a homography is
    what a flat surface seen from an angle actually needs, every other point
    on the desk lands in the right place too.

    The maths: a homography has 8 unknowns, and each corner gives 2 equations,
    so 4 corners is exactly enough. Stack those 8 equations into A @ h = b and
    solve. (This is the "direct linear transform", done the small way.)
    """
    if len(corners) != 4:
        raise ValueError(f"need exactly 4 corners, got {len(corners)}")

    a_rows = []
    b_rows = []

    for (px, py), (dx, dy) in zip(corners, DESK_CORNERS):
        # dx = (h0*px + h1*py + h2) / (h6*px + h7*py + 1), rearranged so the
        # unknowns are all on one side and nothing is divided by anything.
        a_rows.append([px, py, 1, 0, 0, 0, -dx * px, -dx * py])
        b_rows.append(dx)
        a_rows.append([0, 0, 0, px, py, 1, -dy * px, -dy * py])
        b_rows.append(dy)

    a = np.array(a_rows, dtype=float)
    b = np.array(b_rows, dtype=float)

    try:
        h = np.linalg.solve(a, b)
    except np.linalg.LinAlgError as err:
        # Three corners in a line, or two clicked on the same spot.
        raise ValueError(
            "Those four corners do not make a shape. Click the four corners "
            "of the projection, clockwise from the top left."
        ) from err

    return np.append(h, 1.0).reshape(3, 3)


def apply_homography(matrix, point):
    """Map one camera pixel through the matrix to desk coordinates.

    Returns (x, y) as plain floats. The result is NOT clamped to 0..1 - a hand
    beside the desk really is outside the projection, and the caller should be
    able to tell the difference between "at the edge" and "off the desk".
    """
    px, py = point
    vector = matrix @ np.array([px, py, 1.0])
    w = vector[2]

    if abs(w) < 1e-9:
        # The point is on the horizon of the plane. Cannot be on the desk.
        raise ValueError("point does not map onto the desk plane")

    return float(vector[0] / w), float(vector[1] / w)


def on_desk(point, margin=0.0):
    """True when a desk coordinate is inside the projection rectangle."""
    x, y = point
    low = -margin
    high = 1.0 + margin
    return low <= x <= high and low <= y <= high


# ------------------------------------------------------------ calibration --

def save_calibration(corners, frame_size, path=DEFAULT_CALIBRATION_PATH):
    """Write the clicked corners to calibration.json."""
    payload = {
        "corners": [[float(x), float(y)] for x, y in corners],
        "frame_width": int(frame_size[0]),
        "frame_height": int(frame_size[1]),
        "corner_order": list(CORNER_NAMES),
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_calibration(path=DEFAULT_CALIBRATION_PATH):
    """Read calibration.json and hand back (homography, frame_size).

    Raises FileNotFoundError if calibration has never been run, and ValueError
    if the file is there but unusable - both of which the bridge turns into a
    message telling you to run calibrate_desk.py.
    """
    path = Path(path)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"{path.name} is not valid JSON - run calibration again") from err

    try:
        corners = [(float(x), float(y)) for x, y in payload["corners"]]
        frame_size = (int(payload["frame_width"]), int(payload["frame_height"]))
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError(f"{path.name} is missing something - run calibration again") from err

    return corners_to_homography(corners), frame_size


# --------------------------------------------------------------- steadying --

class Smoother:
    """Takes the jitter out of a position without making it feel laggy.

    MediaPipe's landmarks wobble by a few pixels even when a hand is held
    perfectly still, and a dot that shivers looks broken. This is exponential
    smoothing: each new reading is blended with the one before it.

        weight 1.0  = no smoothing at all, every wobble shows
        weight 0.3  = steady, with a little lag behind a fast hand
        weight 0.05 = very smooth, and clearly behind your hand

    Sprint 2 tunes this by eye. Start at 0.35.
    """

    def __init__(self, weight=0.35):
        if not 0 < weight <= 1:
            raise ValueError("weight must be above 0 and at most 1")
        self.weight = weight
        self._value = None

    def reset(self):
        """Forget the last position, so the next one is taken as-is."""
        self._value = None

    def update(self, point):
        """Feed in a new position, get the smoothed one back."""
        x, y = float(point[0]), float(point[1])

        if self._value is None:
            self._value = (x, y)
        else:
            old_x, old_y = self._value
            w = self.weight
            self._value = (old_x + w * (x - old_x), old_y + w * (y - old_y))

        return self._value


class PresenceGate:
    """Stops the dot flickering on and off when detection is patchy.

    A hand has to be seen for `appear_frames` in a row before the dot is
    allowed to appear, and has to be missing for `vanish_frames` in a row
    before it is allowed to go. The two numbers are deliberately different:
    appearing should be quick so the dot feels responsive, and vanishing
    should be slow so one dropped frame does not blink it out.

    This is the software version of what the IR plan called thresholds - and
    it is what Sprint 2's "no false hands over 5 minutes" test leans on.
    """

    def __init__(self, appear_frames=2, vanish_frames=6):
        if appear_frames < 1 or vanish_frames < 1:
            raise ValueError("frame counts must be 1 or more")
        self.appear_frames = appear_frames
        self.vanish_frames = vanish_frames

        self.present = False
        self._seen = 0
        self._missed = 0

    def update(self, detected):
        """Feed in this frame's yes/no. Returns the steadied yes/no."""
        if detected:
            self._seen += 1
            self._missed = 0
            if not self.present and self._seen >= self.appear_frames:
                self.present = True
        else:
            self._missed += 1
            self._seen = 0
            if self.present and self._missed >= self.vanish_frames:
                self.present = False

        return self.present
