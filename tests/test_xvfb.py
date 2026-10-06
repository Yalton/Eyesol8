import os

import pytest
from conftest import (
    FIXTURE,
    needs_godot,
    needs_xvfb,
    screenshot_rgb,
    session_processes,
    wait_for_scene,
)

from eyesol8.backends import get_backend
from eyesol8.session import Session


def godot_gl_command() -> list[str]:
    return [
        "godot", "--audio-driver", "Dummy", "--rendering-driver", "opengl3",
        "--path", str(FIXTURE),
    ]  # fmt: skip


@needs_xvfb
@needs_godot
def test_godot_on_xvfb_screenshot_and_click(no_leaks):
    s = Session.start(godot_gl_command(), cwd=FIXTURE, width=640, height=360, backend="xvfb")
    try:
        assert s.backend.name == "xvfb"
        assert s.display.startswith(":")
        assert s.wayland_display == ""
        assert os.environ.get("DISPLAY") != s.display
        img = wait_for_scene(s, timeout=30)
        assert img.size == (640, 360)

        _, cursor = s.logs()
        assert s.input([{"click": [100, 100]}]) == 1
        s.wait_for_log(r"CLICK \(100\.0, 100\.0\)", timeout=10, since=cursor)
        assert screenshot_rgb(s).getpixel((5, 5)) == (0, 255, 0)
        assert s.screenshot(region=(0, 0, 100, 50), scale=0.5)
    finally:
        s.stop()


@needs_xvfb
def test_xvfb_session_ends_with_program_and_cleans_up(no_leaks):
    s = Session.start(["sh", "-c", "echo hi; exit 4"], backend="xvfb")
    s.wait_for_log("hi", timeout=10)
    result = s.stop()
    assert result.exit_code == 4


@needs_xvfb
def test_two_xvfb_sessions_get_different_displays(no_leaks):
    a = Session.start(["sleep", "60"], backend="xvfb")
    try:
        b = Session.start(["sleep", "60"], backend="xvfb")
        try:
            assert a.display != b.display
            assert a.running and b.running
            tagged = session_processes()
            assert a.pid in tagged and b.pid in tagged
            assert {"Xvfb", "sleep"} <= set(tagged.values())
        finally:
            b.stop()
    finally:
        a.stop()
    assert not a.running and not b.running


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="unknown backend 'nope'"):
        get_backend("nope")


def test_missing_xvfb_raises_clear_error(monkeypatch, tmp_path):
    from eyesol8.backends import MissingDependencyError

    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(MissingDependencyError, match="Xvfb"):
        Session.start(["true"], backend="xvfb")
