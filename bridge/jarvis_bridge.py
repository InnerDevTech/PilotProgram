"""Jarvis Desk bridge: Arduino buttons and knob -> the page on the projector.

    4 buttons + knob -> Arduino Uno -> USB serial -> this file -> web/index.html

The bridge does two jobs at once:

  * it reads lines from the Arduino on a background thread ("BTN 2", "KNOB 30")
    and pushes them to every open browser tab as Server-Sent Events, and
  * it serves the files in web/ over plain HTTP on 127.0.0.1.

Run it from the project root:

    python bridge/jarvis_bridge.py                # find the Arduino and serve
    python bridge/jarvis_bridge.py --no-serial    # no hardware (Sprint 0 demo)
    python bridge/jarvis_bridge.py --list-ports   # which serial ports exist
    python bridge/jarvis_bridge.py --port COM3    # skip auto-detection

It also casts to the projector (the Hccast-..._mira dongle) and opens the page
full screen on it - see bridge/projector.py, and --no-cast to skip that.

The page is at http://localhost:8000. Ctrl+C stops the bridge.

Sprints 2 and 3 add a second Arduino (the hand sensor) and a camera. Those get
their own SerialLink-style class and their own thread; they feed the same
Broadcaster, so nothing in this file has to be rewritten for them.
"""

import argparse
import json
import queue
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Casting to the projector lives in its own file; it is all Windows API calls
# and none of it is needed to serve the page.
try:
    from bridge import projector
except ImportError:  # run as "python bridge/jarvis_bridge.py", no package
    import projector

# pyserial is optional at import time so the tests can run without hardware
# and without the package installed.
try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


# ---------------------------------------------------------------- settings --

# web/ lives next to bridge/, so find it from this file and not from the
# directory the student happened to run the command in.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

BAUD_RATE = 115200
BOOT_WAIT_SECONDS = 2.0      # the Uno restarts when the port opens
RETRY_SECONDS = 2.0          # how long to wait before looking for a port again
BUSY_RETRY_SECONDS = 3.0     # how long to wait when the port is in use
KEEPALIVE_SECONDS = 15.0     # how often to nudge an idle browser tab

# Words that show up in the port name, description or manufacturer of an
# official Uno or a common clone, on Windows, macOS and Linux.
PORT_HINTS = (
    "arduino", "ch340", "wch", "usb-serial", "usb serial",
    "usbserial", "usbmodem", "ttyacm", "ttyusb",
)


# ------------------------------------------------------------- parse a line --

def parse_line(line):
    """Turn one line from the Arduino into an event, or None if it is junk.

    "BTN 2"   -> {"type": "button", "n": 2}
    "KNOB 30" -> {"type": "knob", "minutes": 30}

    Anything else (READY, a half-written line, a value out of range) is None,
    because the serial port picks up noise and we must never crash on it.
    """
    parts = line.strip().split()
    if len(parts) != 2:
        return None

    name, value = parts[0].upper(), parts[1]
    if not value.lstrip("-").isdigit():
        return None
    number = int(value)

    if name == "BTN" and 1 <= number <= 4:
        return {"type": "button", "n": number}
    if name == "KNOB" and 1 <= number <= 60:
        return {"type": "knob", "minutes": number}
    return None


# -------------------------------------------------------------- find a port --

def find_port(ports, preferred=None):
    """Pick the Arduino out of a list of serial ports from list_ports.comports().

    A --port given on the command line always wins. Otherwise the first port
    whose name, description or manufacturer looks like an Arduino is used.
    """
    if preferred:
        return preferred

    for port in ports:
        haystack = " ".join([
            getattr(port, "device", "") or "",
            getattr(port, "description", "") or "",
            getattr(port, "manufacturer", "") or "",
        ]).lower()

        for hint in PORT_HINTS:
            if hint in haystack:
                return port.device

    return None


# ------------------------------------------------------------- broadcaster --

class Broadcaster:
    """Holds one queue per open browser tab and fans events out to all of them."""

    def __init__(self):
        self._clients = []
        self._lock = threading.Lock()

    def add(self):
        """Register a new tab and hand back its own queue."""
        client = queue.Queue()
        with self._lock:
            self._clients.append(client)
        return client

    def remove(self, client):
        """Forget a tab that has gone away."""
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def send(self, event):
        """Put one event on every open tab's queue."""
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            client.put(event)

    def count(self):
        with self._lock:
            return len(self._clients)


# ------------------------------------------------------------ serial thread --

class SerialLink:
    """Talks to one Arduino on a background thread and reconnects by itself.

    The same shape works for the Sprint 2 hand-sensor board: make a second
    SerialLink with its own port and give it the same broadcaster.
    """

    def __init__(self, broadcaster, preferred_port=None, verbose=False):
        self.broadcaster = broadcaster
        self.preferred_port = preferred_port
        self.verbose = verbose

        self.connected = False
        self.port_name = None

        self._serial = None
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._said_waiting = False

    def start(self):
        thread = threading.Thread(target=self._run, name="serial", daemon=True)
        thread.start()

    def stop(self):
        self._stop.set()

    def status_event(self):
        return {"type": "status", "connected": self.connected, "port": self.port_name}

    def send(self, text):
        """Send one command (BEEP, SYNC). Returns True if it went out."""
        with self._write_lock:
            if not self.connected or self._serial is None:
                return False
            try:
                self._serial.write((text + "\n").encode())
                return True
            except Exception:
                # The board was unplugged between the check and the write.
                return False

    # --- the loop: find a port, read from it until it goes away, repeat ---

    def _run(self):
        while not self._stop.is_set():
            if self._open():
                self._read_until_closed()
                self._close()

    def _open(self):
        ports = list_ports.comports()
        device = find_port(ports, self.preferred_port)

        if device is None:
            if not self._said_waiting:
                print("Waiting for the Arduino... (plug it in, or use --port)")
                self._said_waiting = True
            self._stop.wait(RETRY_SECONDS)
            return False

        try:
            self._serial = serial.Serial(device, BAUD_RATE, timeout=1)
        except Exception:
            # Nearly always the Arduino IDE Serial Monitor holding the port.
            print(f"Could not use {device}. Is the Serial Monitor open? "
                  f"Close it - retrying in {int(BUSY_RETRY_SECONDS)} seconds.")
            self._serial = None
            self._stop.wait(BUSY_RETRY_SECONDS)
            return False

        # Opening the port resets the Uno, so let it boot before talking.
        self._stop.wait(BOOT_WAIT_SECONDS)
        try:
            self._serial.reset_input_buffer()
        except Exception:
            pass

        self.port_name = device
        self.connected = True
        self._said_waiting = False

        print(f"Arduino connected on {device}")
        self.send("SYNC")
        self.broadcaster.send(self.status_event())
        return True

    def _read_until_closed(self):
        while not self._stop.is_set():
            try:
                raw = self._serial.readline()
            except Exception:
                print("Arduino unplugged - looking for it again.")
                return

            if not raw:
                continue  # just the one-second read timeout, nothing to do

            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            if self.verbose:
                print(f"  arduino: {line}")

            event = parse_line(line)
            if event is not None:
                self.broadcaster.send(event)

    def _close(self):
        with self._write_lock:
            self.connected = False
            self.port_name = None
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
        self.broadcaster.send(self.status_event())


# ------------------------------------------------------------- http server --

class JarvisServer(ThreadingHTTPServer):
    """Serves web/ plus /events and /beep. One thread per request."""

    daemon_threads = True

    # Left off on purpose: with address reuse on, Windows lets a second bridge
    # bind the same port instead of failing, and the "port is busy" message
    # below would never appear.
    allow_reuse_address = False

    def __init__(self, address, handler, broadcaster, link):
        self.broadcaster = broadcaster
        self.link = link
        super().__init__(address, handler)

    def handle_error(self, request, client_address):
        # A browser tab closing mid-response is normal; no traceback for it.
        pass

    def status_event(self):
        if self.link is None:
            return {"type": "status", "connected": False, "port": None}
        return self.link.status_event()


class JarvisHandler(SimpleHTTPRequestHandler):
    """Static files from web/, with two extra routes bolted on."""

    def log_message(self, fmt, *args):
        # Silence the per-request logging so real messages stand out.
        pass

    def end_headers(self):
        # No caching, so an edit to index.html shows up on a plain reload.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/events":
            self.serve_events()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] != "/beep":
            self.send_error(404)
            return

        link = self.server.link
        if link is not None and link.send("BEEP"):
            self.send_response(200)
        else:
            # No Arduino: the page falls back to beeping by itself.
            self.send_response(503)

        self.send_header("Content-Length", "0")
        self.end_headers()

    def serve_events(self):
        """One Server-Sent Events stream, held open for as long as the tab is."""
        client = self.server.broadcaster.add()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()

            # A new tab needs to know the Arduino state straight away.
            self.write_event(self.server.status_event())

            while True:
                try:
                    event = client.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                else:
                    self.write_event(event)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the tab was closed or reloaded
        finally:
            self.server.broadcaster.remove(client)

    def write_event(self, event):
        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
        self.wfile.flush()


def make_server(http_port, broadcaster, link):
    """Build the HTTP server. Used by main() and by the tests."""
    handler = partial(JarvisHandler, directory=str(WEB_DIR))
    return JarvisServer(("127.0.0.1", http_port), handler, broadcaster, link)


# -------------------------------------------------------------------- main --

def build_parser():
    parser = argparse.ArgumentParser(description="Jarvis Desk bridge: Arduino to browser.")
    parser.add_argument("--port", help="serial port to use instead of auto-detecting")
    parser.add_argument("--no-serial", action="store_true",
                        help="serve the page only, no hardware (Sprint 0 demo)")
    parser.add_argument("--list-ports", action="store_true",
                        help="print every serial port and exit")
    parser.add_argument("--http-port", type=int, default=8000, help="web server port")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open the browser automatically")
    parser.add_argument("--no-cast", action="store_true",
                        help="skip the projector: open a plain browser tab here")
    parser.add_argument("--cast-device", default=projector.DEFAULT_DEVICE_HINT,
                        metavar="NAME",
                        help="part of the wireless display's name "
                             f"(default: {projector.DEFAULT_DEVICE_HINT!r}, "
                             "which matches Hccast-...._mira)")
    parser.add_argument("--cast-mode", choices=("extend", "duplicate", "keep"),
                        default="extend",
                        help="second screen (extend), mirror (duplicate), or "
                             "leave Windows as it is (keep)")
    parser.add_argument("--cast-timeout", type=float, default=45.0,
                        metavar="SECONDS",
                        help="how long to wait for the projector screen")
    parser.add_argument("--list-cast-devices", action="store_true",
                        help="print the wireless displays this PC knows and exit")
    parser.add_argument("--cast-test", action="store_true",
                        help="try the cast on its own, print what the Cast "
                             "panel showed, and exit")
    parser.add_argument("--cast-debug", action="store_true",
                        help="print everything the Cast panel showed - use this "
                             "when the projector is not picked automatically")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every line received from the Arduino")
    # Reserved for later sprints.
    parser.add_argument("--desk", action="store_true", help="hand sensor (Sprint 2)")
    parser.add_argument("--camera", type=int, help="camera number (Sprint 3)")
    return parser


def print_ports():
    for port in list_ports.comports():
        print(f"{port.device}  {port.description}")


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.desk or args.camera is not None:
        print("Not built yet (Sprint 2/3)")

    # pyserial is only needed when we actually touch the hardware, so
    # --no-serial still works without it (that is the Sprint 0 demo).
    if serial is None and not args.no_serial:
        print("Run: python -m pip install -r requirements.txt")
        return 1

    if args.list_cast_devices:
        devices = projector.cast_devices()
        if not devices:
            print("No wireless displays paired. Press Win+K and pick the "
                  "projector once; Windows remembers it after that.")
        for device in devices:
            print(f"{device['name']}  {device['status']}")
        for monitor in projector.list_monitors():
            kind = "primary" if monitor.primary else "second screen"
            print(f"{monitor.name}  {monitor.width}x{monitor.height}  "
                  f"at {monitor.x},{monitor.y}  ({kind})")
        return 0

    if args.cast_test:
        monitor = projector.start_cast(args.cast_device, args.cast_timeout,
                                       debug=True)
        return 0 if monitor is not None else 1

    if args.list_ports:
        if list_ports is None:
            print("Run: python -m pip install -r requirements.txt")
            return 1
        print_ports()
        return 0

    if serial is None:
        print("pyserial is not installed - running without serial.")

    if not WEB_DIR.is_dir():
        print(f"Cannot find the web folder at {WEB_DIR}")
        return 1

    broadcaster = Broadcaster()

    link = None
    if not args.no_serial and serial is not None:
        link = SerialLink(broadcaster, preferred_port=args.port, verbose=args.verbose)
        link.start()

    try:
        server = make_server(args.http_port, broadcaster, link)
    except OSError:
        print(f"Port {args.http_port} is busy - is the bridge already running? "
              f"Try --http-port {args.http_port + 1}")
        return 1

    url = f"http://localhost:{args.http_port}"
    print(f"Jarvis page: {url}   (Ctrl+C to stop)")

    if not args.no_browser:
        if args.no_cast:
            threading.Timer(0.5, webbrowser.open, [url]).start()
        else:
            # Casting waits on Windows and on the dongle, so it goes on its own
            # thread - the page has to be served while the cast is still being
            # set up, or the browser window opens onto nothing.
            threading.Thread(
                target=projector.cast_and_show,
                args=(url, args.cast_device, args.cast_mode, args.cast_timeout,
                      args.cast_debug),
                name="projector", daemon=True,
            ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if link is not None:
            link.stop()
        projector.close_fullscreen()
        server.server_close()
        print("Stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
