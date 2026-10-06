import io
import time

import pytest
from conftest import (
    FIXTURE,
    godot_command,
    needs_gamescope,
    needs_godot,
    wait_for_scene,
)
from PIL import Image

from eyesol8.session import Session


def image(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


@needs_gamescope
@needs_godot
def test_screenshot_godot_scene(no_leaks, tmp_path):
    s = Session.start(godot_command(), cwd=FIXTURE, width=640, height=360)
    try:
        s.wait_for_log(r"Godot Engine", timeout=20)
        wait_for_scene(s)
        saved = tmp_path / "shot.png"
        first = s.screenshot(save_to=saved)
        assert saved.read_bytes() == first
        img = image(first)
        assert img.size == (640, 360)
        assert img.getpixel((5, 5)) == (255, 0, 0)

        time.sleep(0.5)
        assert image(s.screenshot()).tobytes() != img.tobytes(), "white circle did not move"

        assert image(s.screenshot(region=(10, 20, 100, 50))).size == (100, 50)
        assert image(s.screenshot(scale=0.5)).size == (320, 180)
        assert image(s.screenshot(region=(0, 0, 200, 100), scale=0.25)).size == (50, 25)
        with pytest.raises(ValueError):
            s.screenshot(region=(600, 0, 100, 100))
    finally:
        s.stop()
