"""Tests for the projector helper. No projector and no Windows needed.

Only the pure parts are tested: which screen to project onto, which wireless
display is the dongle, where the browser lives and what it is called with. The
parts that press Win+K or talk to Windows are left to the sprint demo.

    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.projector import (  # noqa: E402
    DEFAULT_DEVICE_HINT,
    Monitor,
    find_browser,
    kiosk_command,
    pick_cast_device,
    pick_projector,
)

LAPTOP = Monitor(r"\\.\DISPLAY1", 0, 0, 1920, 1080, True)
PROJECTOR = Monitor(r"\\.\DISPLAY5", 1920, 0, 1024, 768, False)


class PickProjectorTests(unittest.TestCase):

    def test_second_screen_wins(self):
        self.assertEqual(pick_projector([LAPTOP, PROJECTOR]), PROJECTOR)

    def test_order_does_not_matter(self):
        self.assertEqual(pick_projector([PROJECTOR, LAPTOP]), PROJECTOR)

    def test_laptop_only(self):
        self.assertIsNone(pick_projector([LAPTOP]))

    def test_no_screens(self):
        self.assertIsNone(pick_projector([]))


class PickCastDeviceTests(unittest.TestCase):

    def dongle(self, name, status="OK"):
        return {"name": name, "status": status}

    def test_finds_the_desk_dongle(self):
        devices = [self.dongle("Hccast-5B8F5D_mira")]
        self.assertEqual(pick_cast_device(devices, DEFAULT_DEVICE_HINT),
                         "Hccast-5B8F5D_mira")

    def test_ignores_case(self):
        devices = [self.dongle("HCCAST-5B8F5D_MIRA")]
        self.assertEqual(pick_cast_device(devices, "mira"),
                         "HCCAST-5B8F5D_MIRA")

    def test_picks_the_dongle_out_of_several(self):
        devices = [
            self.dongle("Living room TV"),
            self.dongle("Hccast-5B8F5D_mira"),
        ]
        self.assertEqual(pick_cast_device(devices, "mira"),
                         "Hccast-5B8F5D_mira")

    def test_falls_back_when_the_hint_misses(self):
        # A replacement dongle named differently still gets found.
        devices = [self.dongle("HcCast-ABCDEF")]
        self.assertEqual(pick_cast_device(devices, "mira"), "HcCast-ABCDEF")

    def test_no_wireless_display_at_all(self):
        self.assertIsNone(pick_cast_device([], "mira"))

    def test_nothing_matches(self):
        devices = [self.dongle("Living room TV")]
        self.assertIsNone(pick_cast_device(devices, "mira"))


class FindBrowserTests(unittest.TestCase):

    def test_first_one_that_exists_wins(self):
        missing = Path(ROOT) / "no-such-browser.exe"
        real = Path(__file__)  # any file that is really there
        self.assertEqual(find_browser([missing, real]), real)

    def test_nothing_installed(self):
        missing = Path(ROOT) / "no-such-browser.exe"
        self.assertIsNone(find_browser([missing]))


class KioskCommandTests(unittest.TestCase):

    def command(self, monitor=PROJECTOR):
        return kiosk_command(Path("chrome.exe"), "http://localhost:8000",
                             monitor, profile=Path("profile"))

    def test_full_screen_on_the_projector(self):
        command = self.command()
        self.assertIn("--kiosk", command)
        self.assertIn("--window-position=1920,0", command)
        self.assertIn("--window-size=1024,768", command)

    def test_url_is_last(self):
        self.assertEqual(self.command()[-1], "http://localhost:8000")

    def test_own_profile_so_the_flags_are_not_ignored(self):
        self.assertIn("--user-data-dir=profile", self.command())

    def test_no_projector_means_no_position(self):
        command = self.command(monitor=None)
        self.assertIn("--kiosk", command)
        self.assertFalse([flag for flag in command
                          if flag.startswith("--window-position")])


if __name__ == "__main__":
    unittest.main()
