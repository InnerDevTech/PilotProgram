"""Tests for the Jarvis Desk bridge. No Arduino and no pyserial needed.

Run them from the project root:

    python -m unittest discover -s tests -v
"""

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# Let the tests import bridge/jarvis_bridge.py when run from the project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.jarvis_bridge import (  # noqa: E402
    Broadcaster,
    find_port,
    make_server,
    parse_line,
)


class FakePort:
    """Stands in for one entry from serial.tools.list_ports.comports()."""

    def __init__(self, device, description="", manufacturer=""):
        self.device = device
        self.description = description
        self.manufacturer = manufacturer


class ParseLineTests(unittest.TestCase):

    def test_button(self):
        self.assertEqual(parse_line("BTN 2"), {"type": "button", "n": 2})
        self.assertEqual(parse_line("BTN 1"), {"type": "button", "n": 1})
        self.assertEqual(parse_line("BTN 4"), {"type": "button", "n": 4})

    def test_knob(self):
        self.assertEqual(parse_line("KNOB 1"), {"type": "knob", "minutes": 1})
        self.assertEqual(parse_line("KNOB 30"), {"type": "knob", "minutes": 30})
        self.assertEqual(parse_line("KNOB 60"), {"type": "knob", "minutes": 60})

    def test_out_of_range(self):
        self.assertIsNone(parse_line("BTN 0"))
        self.assertIsNone(parse_line("BTN 9"))
        self.assertIsNone(parse_line("KNOB 0"))
        self.assertIsNone(parse_line("KNOB 61"))

    def test_junk(self):
        self.assertIsNone(parse_line("hello"))
        self.assertIsNone(parse_line("BTN"))
        self.assertIsNone(parse_line("BTN two"))
        self.assertIsNone(parse_line("BTN 2 3"))
        self.assertIsNone(parse_line("\x00\xff rubbish"))

    def test_empty(self):
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("   "))
        self.assertIsNone(parse_line("\r\n"))

    def test_extra_spaces(self):
        self.assertEqual(parse_line("  BTN   3  \r\n"), {"type": "button", "n": 3})
        self.assertEqual(parse_line("\tKNOB\t15\n"), {"type": "knob", "minutes": 15})

    def test_ready_is_ignored(self):
        self.assertIsNone(parse_line("READY"))


class FindPortTests(unittest.TestCase):

    def test_official_uno(self):
        ports = [
            FakePort("COM1", "Communications Port", "Microsoft"),
            FakePort("COM3", "Arduino Uno (COM3)", "Arduino LLC"),
        ]
        self.assertEqual(find_port(ports), "COM3")

    def test_ch340_clone(self):
        ports = [FakePort("/dev/ttyUSB0", "USB2.0-Serial CH340", "wch.cn")]
        self.assertEqual(find_port(ports), "/dev/ttyUSB0")

    def test_no_match(self):
        ports = [
            FakePort("COM1", "Communications Port", "Microsoft"),
            FakePort("COM2", "Bluetooth link", "Intel"),
        ]
        self.assertIsNone(find_port(ports))

    def test_no_ports_at_all(self):
        self.assertIsNone(find_port([]))

    def test_preferred_port_wins(self):
        ports = [FakePort("COM3", "Arduino Uno (COM3)", "Arduino LLC")]
        self.assertEqual(find_port(ports, preferred="COM7"), "COM7")


class BroadcasterTests(unittest.TestCase):

    def test_both_clients_get_the_event(self):
        broadcaster = Broadcaster()
        first = broadcaster.add()
        second = broadcaster.add()

        event = {"type": "button", "n": 1}
        broadcaster.send(event)

        self.assertEqual(first.get_nowait(), event)
        self.assertEqual(second.get_nowait(), event)

    def test_removed_client_stops_receiving(self):
        broadcaster = Broadcaster()
        staying = broadcaster.add()
        leaving = broadcaster.add()

        broadcaster.remove(leaving)
        broadcaster.send({"type": "button", "n": 2})

        self.assertEqual(staying.get_nowait(), {"type": "button", "n": 2})
        self.assertTrue(leaving.empty())
        self.assertEqual(broadcaster.count(), 1)

    def test_removing_twice_is_harmless(self):
        broadcaster = Broadcaster()
        client = broadcaster.add()
        broadcaster.remove(client)
        broadcaster.remove(client)
        self.assertEqual(broadcaster.count(), 0)


class ServerTests(unittest.TestCase):
    """The --no-serial setup: a real HTTP server on a free port, no hardware."""

    @classmethod
    def setUpClass(cls):
        cls.broadcaster = Broadcaster()
        cls.server = make_server(0, cls.broadcaster, None)  # 0 = any free port
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_root_serves_index_html(self):
        with urllib.request.urlopen(self.url + "/", timeout=5) as response:
            body = response.read().decode("utf-8", errors="ignore")
        self.assertEqual(response.status, 200)
        self.assertIn("Jarvis Desk", body)

    def test_events_sends_status_first(self):
        with urllib.request.urlopen(self.url + "/events", timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "text/event-stream")
            line = response.readline().decode()

        self.assertTrue(line.startswith("data: "), line)
        event = json.loads(line[len("data: "):])
        self.assertEqual(event, {"type": "status", "connected": False, "port": None})

    def test_beep_without_arduino_is_503(self):
        request = urllib.request.Request(self.url + "/beep", method="POST", data=b"")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
