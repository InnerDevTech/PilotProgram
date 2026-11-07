"""Tell the bridge where the projection is, as the camera sees it.

The webcam looks down at the desk and sees the whole room - the desk, the
floor, your keyboard, the lot. It has no idea which part of that picture is
the bit the projector is drawing on. This tool is how you tell it: you click
the four corners of the projection once, and it saves them to
calibration.json. From then on the bridge can turn any camera pixel into a
desk coordinate.

Run it with the projector ON and showing the page, so you can actually see
the rectangle you are clicking:

    python bridge/calibrate_desk.py                # camera 0
    python bridge/calibrate_desk.py --camera 1     # a different camera
    python bridge/calibrate_desk.py --check        # look at the saved answer

Click the four corners CLOCKWISE FROM THE TOP LEFT OF THE PROJECTED PAGE -
the corners as you read the page standing at the desk, NOT as they sit in the
camera window. The camera may see the desk from another side or mirrored, so
the page's top left can be anywhere in its picture; touch each corner with a
finger and click where the finger shows up. Get this wrong and the dot moves
backwards. Then:

    s   save and quit
    r   start the clicking again
    q   quit without saving

Re-run this whenever the camera or the projector gets bumped. There is no way
for the software to notice that it happened, so mark their positions with tape
and re-run after any move.
"""

import argparse
import sys
from pathlib import Path

try:
    import cv2
except ImportError:  # pragma: no cover - the message is the whole point
    cv2 = None

# Works both as "python bridge/calibrate_desk.py" and as part of the package.
if __package__:
    from bridge import camera, desk_geometry
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from bridge import camera, desk_geometry


WINDOW = "Jarvis Desk calibration"

# Colours are BGR here, not RGB - that is an OpenCV thing, not a typo.
GREEN = (120, 240, 120)
AMBER = (60, 190, 250)
WHITE = (255, 255, 255)


# ------------------------------------------------------------- the drawing --

def draw_overlay(frame, corners):
    """Put the clicked corners, the outline and the instructions on a frame."""
    shown = frame.copy()

    for index, (x, y) in enumerate(corners):
        point = (int(x), int(y))
        cv2.circle(shown, point, 7, GREEN, -1)
        cv2.circle(shown, point, 12, GREEN, 2)
        cv2.putText(shown, str(index + 1), (point[0] + 16, point[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, GREEN, 2)

    # Join them up as they are clicked, and close the shape on the fourth.
    for index in range(len(corners) - 1):
        cv2.line(shown, _as_int(corners[index]), _as_int(corners[index + 1]), GREEN, 2)
    if len(corners) == 4:
        cv2.line(shown, _as_int(corners[3]), _as_int(corners[0]), GREEN, 2)
        _draw_grid(shown, corners)

    if len(corners) < 4:
        message = f"Click the {desk_geometry.CORNER_NAMES[len(corners)]} corner"
        colour = AMBER
    else:
        message = "s = save    r = redo    q = quit"
        colour = GREEN

    cv2.rectangle(shown, (0, 0), (shown.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(shown, message, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
    return shown


def _as_int(point):
    return (int(point[0]), int(point[1]))


def _draw_grid(frame, corners, steps=4):
    """Draw the desk's own grid back onto the camera picture.

    This is the check that matters: if the homography is right, these lines
    land square on the projected page. If they lean or bulge, a corner was
    clicked in the wrong place or in the wrong order.
    """
    try:
        matrix = desk_geometry.corners_to_homography(corners)
    except ValueError:
        return

    inverse = _invert(matrix)
    if inverse is None:
        return

    for i in range(1, steps):
        t = i / steps
        _line_through(frame, inverse, (t, 0.0), (t, 1.0))
        _line_through(frame, inverse, (0.0, t), (1.0, t))


def _invert(matrix):
    import numpy as np

    try:
        return np.linalg.inv(matrix)
    except np.linalg.LinAlgError:
        return None


def _line_through(frame, inverse, start, end):
    try:
        a = desk_geometry.apply_homography(inverse, start)
        b = desk_geometry.apply_homography(inverse, end)
    except ValueError:
        return
    cv2.line(frame, _as_int(a), _as_int(b), WHITE, 1)


# -------------------------------------------------------------- the camera --

def open_camera(choice):
    """Open a webcam (by name or number) and give back (capture, width, height).

    CAP_DSHOW is asked for first because on Windows the default backend can
    take ten seconds to open a camera, and sometimes opens it at the wrong
    resolution.
    """
    index = camera.resolve(choice)
    capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not capture.isOpened():
        capture = cv2.VideoCapture(index)

    if not capture.isOpened():
        return None, 0, 0

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    return capture, width, height


# ----------------------------------------------------------------- the run --

def calibrate(camera_choice, path):
    try:
        capture, width, height = open_camera(camera_choice)
    except LookupError as err:
        print(err)
        return 1

    if capture is None:
        print(f"Cannot open {camera.describe(camera_choice)}.")
        print("Is another program using it? List cameras with: python bridge/camera.py")
        return 1

    print(f"Opened {camera.describe(camera_choice)} at {width}x{height}.")
    print("Click the four corners of the projected PAGE, clockwise from its top left.")
    print("Go by the page as you read it at the desk, not by the camera picture -")
    print("touch each corner with a finger and click where the finger appears.")

    corners = []

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN and len(corners) < 4:
            corners.append((float(x), float(y)))
            print(f"  {len(corners)}. {desk_geometry.CORNER_NAMES[len(corners) - 1]}"
                  f" at ({x}, {y})")

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                print("The camera stopped sending frames.")
                return 1

            cv2.imshow(WINDOW, draw_overlay(frame, corners))
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("Quit without saving.")
                return 1

            if key == ord("r"):
                corners.clear()
                print("Starting again - click the top left corner.")

            if key == ord("s"):
                if len(corners) < 4:
                    print(f"Only {len(corners)} corners clicked. Need 4.")
                    continue
                try:
                    desk_geometry.corners_to_homography(corners)
                except ValueError as err:
                    print(err)
                    continue

                desk_geometry.save_calibration(corners, (width, height), path=path)
                print(f"Saved to {path}")
                print("Start the bridge with --camera to use it.")
                return 0
    finally:
        capture.release()
        cv2.destroyAllWindows()


def check(path):
    """Print the saved calibration, without touching the camera."""
    try:
        matrix, frame_size = desk_geometry.load_calibration(path=path)
    except FileNotFoundError:
        print("No calibration yet. Run: python bridge/calibrate_desk.py")
        return 1
    except ValueError as err:
        print(err)
        return 1

    print(f"Calibration from {path}")
    print(f"  camera frame: {frame_size[0]}x{frame_size[1]}")
    print("  a few spot checks, camera pixel -> desk coordinate:")

    middle = (frame_size[0] / 2, frame_size[1] / 2)
    for label, pixel in (("frame centre", middle), ("top left pixel", (0, 0))):
        x, y = desk_geometry.apply_homography(matrix, pixel)
        where = "on the desk" if desk_geometry.on_desk((x, y)) else "off the desk"
        print(f"    {label:>15}: ({x:+.3f}, {y:+.3f})  {where}")

    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", default=camera.DEFAULT_CAMERA,
                        help=f"camera name or number (default {camera.DEFAULT_CAMERA})")
    parser.add_argument("--check", action="store_true",
                        help="print the saved calibration and exit")
    parser.add_argument("--file", default=str(desk_geometry.DEFAULT_CALIBRATION_PATH),
                        help="where calibration.json lives")
    args = parser.parse_args(argv)

    if args.check:
        return check(args.file)

    if cv2 is None:
        print("OpenCV is not installed. Run: python -m pip install -r requirements.txt")
        return 1

    return calibrate(args.camera, args.file)


if __name__ == "__main__":
    raise SystemExit(main())
