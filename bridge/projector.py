"""Put the Jarvis page onto the projector, full screen, without touching a mouse.

The projector here is a Miracast dongle - the one on this desk reports itself as
"Hccast-5B8F5D_mira". Three steps, and the bridge runs them at startup:

  1. cast to the dongle, unless Windows is already casting to it,
  2. make it a second screen (extend) so the desk page gets it to itself,
  3. open the page in a kiosk-mode browser window placed on that screen.

Step 1 is the only fragile part. There is no command line for "connect to a
wireless display", so we press Win+K and click the device in the Cast panel
through UI Automation. If that does not work the bridge says what to click and
carries on waiting for the screen to appear - a failed cast must never stop the
demo.

Windows only. On macOS and Linux every function here is a no-op and the bridge
falls back to opening an ordinary browser tab.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from collections import namedtuple
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# Matches "Hccast-5B8F5D_mira". Deliberately short: the dongle puts part of its
# MAC address in the middle of its name, so a new dongle will not match a
# hard-coded full name but will still match this.
DEFAULT_DEVICE_HINT = "mira"

# Other spellings of the same dongle family, tried if the hint matches nothing.
FALLBACK_HINTS = ("hccast", "hcast", "miracast", "cast")

POLL_SECONDS = 1.0            # how often to look for the projector screen
PANEL_SETTLE_SECONDS = 1.5    # let the Cast panel draw before clicking in it
KIOSK_PROFILE = Path(tempfile.gettempdir()) / "jarvis-desk-kiosk"

Monitor = namedtuple("Monitor", "name x y width height primary")

# The kiosk browser we started, so Ctrl+C can close it again. A kiosk window
# has no close button, so leaving one behind means a stuck full-screen page on
# the projector.
_kiosk = None


# ----------------------------------------------------------------- monitors --

def list_monitors():
    """Every screen Windows currently has, in physical pixels.

    Returns [] off Windows, so callers can treat "no projector" and "not
    Windows" the same way.
    """
    if not IS_WINDOWS:
        return []

    import ctypes
    from ctypes import wintypes

    # Ask for physical pixels. Without this the coordinates come back scaled
    # and the browser window lands on the wrong screen when the laptop panel
    # is at 125% and the projector is not.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    MONITORINFOF_PRIMARY = 1
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(RECT), wintypes.LPARAM,
    )

    found = []

    def on_monitor(handle, _hdc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            box = info.rcMonitor
            found.append(Monitor(
                name=info.szDevice,
                x=box.left,
                y=box.top,
                width=box.right - box.left,
                height=box.bottom - box.top,
                primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
            ))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(
        None, None, callback_type(on_monitor), 0)
    return found


def pick_projector(monitors):
    """Choose the screen to project onto: the first one that is not primary.

    With one screen there is nothing to pick and this returns None - the caller
    then shows the page on the laptop panel instead, which is the right answer
    for working on the code without a projector plugged in.
    """
    for monitor in monitors:
        if not monitor.primary:
            return monitor
    return None


# ------------------------------------------------------------- cast devices --

def _powershell(script, timeout=30):
    """Run a PowerShell snippet and hand back its stdout, or "" if it failed."""
    if not IS_WINDOWS:
        return ""
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return ""
    return done.stdout.strip()


def cast_devices():
    """Known wireless displays, as [{"name": ..., "status": ...}, ...].

    These are the paired Miracast sinks Windows remembers, whether or not one
    is being cast to right now.
    """
    raw = _powershell(
        "Get-PnpDevice -Class Miracast -ErrorAction SilentlyContinue | "
        "Select-Object FriendlyName,Status | ConvertTo-Json -Compress"
    )
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if isinstance(parsed, dict):          # one device comes back unwrapped
        parsed = [parsed]

    devices = []
    for item in parsed:
        name = (item.get("FriendlyName") or "").strip()
        if name:
            devices.append({"name": name, "status": item.get("Status") or ""})
    return devices


def pick_cast_device(devices, hint=DEFAULT_DEVICE_HINT):
    """Find the projector dongle in a list from cast_devices().

    The hint is matched case-insensitively anywhere in the name, so "mira"
    finds "Hccast-5B8F5D_mira". If it matches nothing, the spellings in
    FALLBACK_HINTS are tried in turn before giving up.
    """
    for needle in [hint] + [h for h in FALLBACK_HINTS if h != hint]:
        if not needle:
            continue
        lowered = needle.lower()
        for device in devices:
            if lowered in device["name"].lower():
                return device["name"]
    return None


def is_casting():
    """True if a Miracast screen is attached right now."""
    raw = _powershell(
        "@(Get-PnpDevice -Class Monitor -ErrorAction SilentlyContinue | "
        "Where-Object { $_.FriendlyName -match 'Miracast' -and "
        "$_.Status -eq 'OK' }).Count"
    )
    return raw.strip().isdigit() and int(raw.strip()) > 0


# ------------------------------------------------------------ start the cast --

def open_cast_panel():
    """Press Win+K, which opens the Cast panel.

    SendInput and not the older keybd_event: keybd_event's Win key is ignored
    in some sessions, and this one has to work from a background thread of a
    console program.
    """
    if not IS_WINDOWS:
        return False

    import ctypes
    from ctypes import wintypes

    VK_LWIN, VK_K = 0x5B, 0x4B
    INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

    class INPUT(ctypes.Structure):
        # The real INPUT union is wider than its keyboard arm, so pad it out
        # or SendInput rejects the size.
        _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT),
                    ("padding", ctypes.c_ubyte * 8)]

    def key(code, up=False):
        event = INPUT()
        event.type = INPUT_KEYBOARD
        event.ki.wVk = code
        event.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        return event

    strokes = (INPUT * 4)(key(VK_LWIN), key(VK_K),
                          key(VK_K, up=True), key(VK_LWIN, up=True))
    try:
        sent = ctypes.windll.user32.SendInput(
            4, ctypes.byref(strokes), ctypes.sizeof(INPUT))
        return sent == 4
    except Exception:
        return False


# Finding the Cast panel and clicking the right thing in it takes more care
# than it sounds like, so it lives in its own PowerShell script next to this
# file - the comment at the top of cast_click.ps1 explains why.
CLICK_SCRIPT = Path(__file__).resolve().parent / "cast_click.ps1"

# Where the script's account of what it saw goes when a click misses.
DUMP_FILE = Path(tempfile.gettempdir()) / "jarvis-cast-dump.txt"


def click_cast_device(name, timeout=25, tokens=FALLBACK_HINTS, dump=False):
    """Click a device by name in the open Cast panel.

    Returns (clicked, report). The report is the script's own output; on a miss
    it lists every shell window it looked in and everything named inside them,
    which is the only way to tell a panel that never opened from a device the
    panel labels differently than Windows reports it.
    """
    if not IS_WINDOWS:
        return False, ""
    if not CLICK_SCRIPT.is_file():
        return False, f"{CLICK_SCRIPT} is missing"

    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(CLICK_SCRIPT),
        "-Needle", name,
        "-TimeoutSeconds", str(int(timeout)),
        "-Tokens", ",".join(tokens),
    ]
    if dump:
        command.append("-Dump")

    try:
        # PowerShell writes UTF-8 here, which is not the console's code page,
        # so say so or the dump comes back full of mojibake.
        done = subprocess.run(command, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout + 30)
    except Exception as problem:
        return False, f"could not run cast_click.ps1: {problem}"

    report = (done.stdout or "").strip()
    return report.startswith("CLICKED"), report


def set_display_mode(mode):
    """Switch between extend and duplicate with the built-in DisplaySwitch.exe."""
    flags = {"extend": "/extend", "duplicate": "/clone"}
    if mode not in flags or not IS_WINDOWS:
        return False
    try:
        subprocess.run(["DisplaySwitch.exe", flags[mode]], timeout=20)
        return True
    except Exception:
        return False


def wait_for_projector(timeout=45):
    """Poll until a second screen shows up. Returns the Monitor, or None."""
    deadline = time.monotonic() + timeout
    while True:
        monitor = pick_projector(list_monitors())
        if monitor is not None:
            return monitor
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_SECONDS)


def start_cast(hint=DEFAULT_DEVICE_HINT, timeout=45, debug=False):
    """Get Windows casting to the projector. Returns its Monitor, or None.

    Already casting, or the projector plugged in over HDMI, is the easy case:
    the screen is already there and nothing is pressed.
    """
    existing = pick_projector(list_monitors())
    if existing is not None:
        print(f"Projector screen already there ({existing.width}x{existing.height}).")
        return existing

    devices = cast_devices()
    device = pick_cast_device(devices, hint)

    if device is None:
        known = ", ".join(d["name"] for d in devices) or "none"
        print(f"No wireless display matching '{hint}' is paired with this PC "
              f"(Windows knows: {known}).")
        print("Opening the Cast panel - pick the projector there.")
    else:
        print(f"Casting to {device}...")

    open_cast_panel()
    time.sleep(PANEL_SETTLE_SECONDS)

    if device is not None:
        # A Miracast dongle takes several seconds to turn up in the panel after
        # it opens, so keep looking for most of the time we have rather than
        # giving up on the first pass.
        click_timeout = max(20, min(timeout - 10, 40))
        clicked, report = click_cast_device(device, timeout=click_timeout,
                                            dump=debug)
        if debug and report:
            print(report)
        if not clicked:
            print(f"Could not click {device} in the Cast panel. "
                  f"Click it by hand - still waiting.")
            try:
                DUMP_FILE.write_text(report or "(no output)", encoding="utf-8")
                print(f"What the panel looked like: {DUMP_FILE}")
            except Exception:
                pass

    monitor = wait_for_projector(timeout)
    if monitor is None:
        print(f"No projector screen after {int(timeout)}s. "
              f"Press Win+K, pick the projector, then reload the page.")
    else:
        print(f"Projector connected ({monitor.width}x{monitor.height}).")
    return monitor


# ------------------------------------------------------- the browser window --

def browser_candidates():
    """Where Chrome and then Edge usually live on Windows, best first."""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get(
        "ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_app_data = os.environ.get("LOCALAPPDATA", "")

    return [
        Path(program_files) / r"Google\Chrome\Application\chrome.exe",
        Path(program_files_x86) / r"Google\Chrome\Application\chrome.exe",
        Path(local_app_data or ".") / r"Google\Chrome\Application\chrome.exe",
        Path(program_files_x86) / r"Microsoft\Edge\Application\msedge.exe",
        Path(program_files) / r"Microsoft\Edge\Application\msedge.exe",
    ]


def find_browser(candidates=None):
    """First Chrome or Edge that exists, or None. Both take the same flags."""
    for path in (candidates if candidates is not None else browser_candidates()):
        if Path(path).is_file():
            return Path(path)
    return None


def kiosk_command(browser, url, monitor=None, profile=KIOSK_PROFILE):
    """Build the browser command line. Pure, so the tests can check it.

    --kiosk is what makes it full screen with no tabs and no address bar, and
    --window-position decides which screen it goes full screen on. The separate
    --user-data-dir matters: without it an already-running Chrome just opens a
    tab in the window it has and ignores every flag here.
    """
    command = [
        str(browser),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--new-window",
        "--kiosk",
    ]
    if monitor is not None:
        command.append(f"--window-position={monitor.x},{monitor.y}")
        command.append(f"--window-size={monitor.width},{monitor.height}")
    command.append(url)
    return command


def open_fullscreen(url, monitor=None):
    """Open the page full screen, on the projector if we have one.

    Falls back to the default browser (a normal window - press F11 yourself)
    if neither Chrome nor Edge is installed.
    """
    browser = find_browser()
    if browser is None:
        import webbrowser
        print("No Chrome or Edge found - opening the default browser. "
              "Press F11 for full screen.")
        webbrowser.open(url)
        return False

    global _kiosk
    try:
        KIOSK_PROFILE.mkdir(parents=True, exist_ok=True)
        _kiosk = subprocess.Popen(kiosk_command(browser, url, monitor))
    except Exception as problem:
        print(f"Could not start {browser.name} ({problem}). "
              f"Open {url} by hand and press F11.")
        return False

    where = "the projector" if monitor is not None else "this screen"
    print(f"Desk page full screen on {where}. Press Alt+F4 or Ctrl+C to stop.")
    return True


def close_fullscreen():
    """Close the kiosk window the bridge opened, if it is still up."""
    global _kiosk
    process, _kiosk = _kiosk, None
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


# --------------------------------------------------------------- everything --

def cast_and_show(url, hint=DEFAULT_DEVICE_HINT, mode="extend", timeout=45,
                  debug=False):
    """Cast to the projector and put the page on it full screen.

    Never raises: every step prints what went wrong and the next one carries
    on, because losing the projector should cost you a screen and not the
    whole bridge.
    """
    if not IS_WINDOWS:
        print("Casting is Windows-only - opening the page here instead.")
        open_fullscreen(url, pick_projector(list_monitors()))
        return

    monitor = None
    try:
        monitor = start_cast(hint, timeout, debug)
    except Exception as problem:
        print(f"Casting failed ({problem}). Showing the page on this screen.")

    if monitor is not None and mode in ("extend", "duplicate"):
        set_display_mode(mode)
        # Extending renumbers and moves the screens, so ask again for the
        # projector's real position before placing the window on it.
        time.sleep(POLL_SECONDS)
        monitor = pick_projector(list_monitors()) or monitor

    open_fullscreen(url, monitor)
