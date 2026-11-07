"""Tests for picking the webcam by name. No camera or pygrabber needed."""

import pytest

from bridge import camera

NAMES = ["OBS Virtual Camera", "USB Video (No Signal)", "Logi C270 HD WebCam"]


def test_number_passes_straight_through():
    assert camera.resolve(2) == 2
    assert camera.resolve("1") == 1
    assert camera.resolve(" 0 ") == 0


def test_name_finds_its_number():
    assert camera.resolve("C270", NAMES) == 2


def test_name_match_ignores_case_and_takes_part_of_the_name():
    assert camera.resolve("c270", NAMES) == 2
    assert camera.resolve("logi", NAMES) == 2


def test_first_match_wins():
    assert camera.resolve("camera", ["Cam A camera", "Cam B camera"]) == 0


def test_missing_camera_lists_what_is_there():
    with pytest.raises(LookupError) as err:
        camera.resolve("C920", NAMES)
    message = str(err.value)
    assert "C920" in message
    assert "2: Logi C270 HD WebCam" in message


def test_no_name_list_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(camera, "list_cameras", lambda: None)
    with pytest.raises(LookupError) as err:
        camera.resolve("C270")
    assert "pygrabber" in str(err.value)
    assert "--camera 1" in str(err.value)


def test_default_is_the_c270():
    assert camera.DEFAULT_CAMERA == "C270"
