import pytest
from conftest import (
    FIXTURE,
    godot_command,
    needs_gamescope,
    needs_godot,
    screenshot_rgb,
    wait_for_scene,
)

from eyesol8.session import Session


@needs_gamescope
@needs_godot
def test_keyboard_and_mouse_reach_godot(no_leaks):
    s = Session.start(godot_command(), cwd=FIXTURE, width=640, height=360)
    try:
        s.wait_for_log(r"Godot Engine", timeout=20)
        wait_for_scene(s)
        _, cursor = s.logs()
        assert s.input([{"key": "space"}]) == 1
        s.wait_for_log(r"KEY Space", timeout=10, since=cursor)

        assert s.input([{"key": "ctrl+s"}, {"text": "Hi!"}]) == 2
        s.wait_for_log(r"KEY Ctrl\+S", timeout=10, since=cursor)
        s.wait_for_log(r"KEY Shift\+1", timeout=10, since=cursor)

        assert s.input([{"click": [100, 100], "button": 1}]) == 1
        s.wait_for_log(r"CLICK \(100\.0, 100\.0\)", timeout=10, since=cursor)
        assert screenshot_rgb(s).getpixel((5, 5)) == (0, 255, 0)
    finally:
        s.stop()


@needs_gamescope
def test_invalid_actions_send_nothing(no_leaks):
    s = Session.start(["sleep", "60"])
    try:
        with pytest.raises(ValueError, match="NoSuchKey"):
            s.input([{"key": "a"}, {"key": "NoSuchKey"}])
        with pytest.raises(ValueError, match="exactly one"):
            s.input([{"key": "a", "click": [1, 1]}])
        with pytest.raises(ValueError):
            s.input([{"click": [1]}])
        assert s.input([{"move": [10, 10]}, {"scroll": -2}, {"drag": [[1, 1], [50, 50]]}]) == 3
    finally:
        s.stop()
