"""Find the desk webcam by name, so its number never matters.

OpenCV only knows cameras by number, and Windows hands those numbers out in
whatever order the devices turned up - plug in a capture card or install OBS
and the Logitech C270 quietly stops being camera 0. Asking for it by name
avoids all of that.

    python bridge/camera.py          # list every camera and its number

Every script takes --camera, which can be a name or a number:

    --camera C270        # the default: the first camera whose name has "C270"
    --camera 1           # a plain number still works

The names come from DirectShow via pygrabber (Windows only). Without it, a
name cannot be looked up, and the error says to install it or pass a number.
"""

import sys

# The desk webcam. Any part of the name Windows shows for it is enough.
DEFAULT_CAMERA = "C270"


def list_cameras():
    """Camera names in OpenCV's DirectShow order, or None if that can't be known."""
    if sys.platform != "win32":
        return None
    try:
        from pygrabber.dshow_graph import FilterGraph
    except ImportError:
        return None
    try:
        return list(FilterGraph().get_input_devices())
    except Exception:  # pragma: no cover - COM can fail in odd ways
        return None


def resolve(choice, names=None):
    """Turn a --camera value into an OpenCV camera number.

    A number (or a string of digits) is used as-is. Anything else is matched,
    ignoring case, against the camera names. `names` is only passed in tests.
    Raises LookupError with a message worth printing as-is.
    """
    if isinstance(choice, int):
        return choice
    text = str(choice).strip()
    if text.isdigit():
        return int(text)

    if names is None:
        names = list_cameras()
    if names is None:
        raise LookupError(
            f'Cannot look up a camera called "{text}": that needs pygrabber '
            "(Windows only).\n"
            "Run: python -m pip install pygrabber\n"
            "or pass a number instead, e.g. --camera 1")

    for index, name in enumerate(names):
        if text.lower() in name.lower():
            return index

    found = "\n".join(f"    {i}: {n}" for i, n in enumerate(names)) or "    (none)"
    raise LookupError(
        f'No camera with "{text}" in its name. Is it plugged in?\n'
        f"Cameras Windows can see:\n{found}")


def describe(choice):
    """A short label for messages: 'C270 (camera 1)' or 'camera 1'."""
    try:
        index = resolve(choice)
    except LookupError:
        return f'"{choice}"'
    text = str(choice).strip()
    return f"camera {index}" if text.isdigit() else f"{text} (camera {index})"


def main():
    names = list_cameras()
    if names is None:
        print("Camera names need pygrabber on Windows: "
              "python -m pip install pygrabber")
        return 1
    if not names:
        print("Windows reports no cameras.")
        return 1
    for index, name in enumerate(names):
        mark = "   <- default" if DEFAULT_CAMERA.lower() in name.lower() else ""
        print(f"{index}: {name}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
