import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import screenshot_rgb

from eyesol8.session import Session

GTK_APP = Path(__file__).parent / "fixtures" / "gtk_app.py"


def _has_gtk4() -> bool:
    probe = "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk"
    result = subprocess.run(["/usr/bin/python3", "-c", probe], capture_output=True, check=False)
    return result.returncode == 0


needs_wlroots = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cage", "grim", "wtype")) or not _has_gtk4(),
    reason="needs cage, grim, wtype and system python3 with GTK 4 (PyGObject)",
)


def wait_for_color(session, color, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        img = screenshot_rgb(session)
        if img.getpixel((5, 5)) == color:
            return img
        if time.monotonic() > deadline:
            raise TimeoutError(f"pixel (5, 5) = {img.getpixel((5, 5))}, wanted {color}")
        time.sleep(0.1)


@pytest.fixture
def gtk(no_leaks):
    s = Session.start(["/usr/bin/python3", str(GTK_APP)], backend="wlroots")
    try:
        s.wait_for_log(r"GTK ready on wayland-", timeout=15)
        yield s
    finally:
        s.stop()


@needs_wlroots
def test_gtk4_screenshot_keys_and_clicks(gtk):
    assert gtk.backend.name == "wlroots"
    img = wait_for_color(gtk, (255, 0, 0))
    assert img.size == (1280, 720)

    _, cursor = gtk.logs()
    assert gtk.input([{"key": "a"}, {"key": "ctrl+s"}, {"text": "Hi"}]) == 3
    gtk.wait_for_log(r"^KEY i$", timeout=5, since=cursor)
    keys = [line for line in gtk.logs(since=cursor)[0] if line.startswith("KEY")]
    assert keys == ["KEY a", "KEY s", "KEY H", "KEY i"]
    wait_for_color(gtk, (0, 0, 255))

    _, cursor = gtk.logs()
    assert gtk.input([{"click": [100, 100]}, {"click": [640, 360], "button": 3}]) == 2
    gtk.wait_for_log(r"CLICK \(640\.0, 360\.0\) button 3", timeout=5, since=cursor)
    assert "CLICK (100.0, 100.0) button 1" in gtk.logs(since=cursor)[0]
    wait_for_color(gtk, (0, 255, 0))


@needs_wlroots
def test_gtk4_drag_scroll_and_validation(gtk):
    _, cursor = gtk.logs()
    assert gtk.input([{"drag": [[10, 10], [300, 200]]}, {"scroll": 2}, {"move": [5, 5]}]) == 3
    gtk.wait_for_log(r"CLICK \(10\.0, 10\.0\)", timeout=5, since=cursor)
    with pytest.raises(ValueError, match="unknown key"):
        gtk.input([{"key": "a"}, {"key": "NoSuchKey"}])
    with pytest.raises(ValueError, match="button must be"):
        gtk.input([{"click": [1, 1], "button": 9}])
    with pytest.raises(TypeError):
        gtk.input(["a"])


@needs_wlroots
def test_wlroots_custom_size(no_leaks):
    s = Session.start(["/usr/bin/python3", str(GTK_APP)], backend="wlroots", width=640, height=360)
    try:
        s.wait_for_log(r"GTK ready on wayland-", timeout=15)
        img = wait_for_color(s, (255, 0, 0))
        assert img.size == (640, 360)
        assert img.getpixel((635, 355)) == (255, 0, 0)

        _, cursor = s.logs()
        assert s.input([{"click": [100, 100]}, {"click": [600, 340]}]) == 2
        s.wait_for_log(r"CLICK \(600\.0, 340\.0\)", timeout=5, since=cursor)
        assert "CLICK (100.0, 100.0) button 1" in s.logs(since=cursor)[0]
        wait_for_color(s, (0, 255, 0))
    finally:
        s.stop()


@needs_wlroots
def test_wlroots_rejects_bad_size(no_leaks):
    with pytest.raises(ValueError, match="positive"):
        Session.start(["true"], backend="wlroots", width=0, height=360)


@needs_wlroots
def test_wlroots_uses_private_socket(gtk):
    runtime = gtk.backend.runtime_dir
    assert runtime.is_relative_to(gtk.session_dir)
    assert (runtime / gtk._display_values["WAYLAND_DISPLAY"]).is_socket()
