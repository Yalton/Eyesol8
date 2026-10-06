import io
import os

import pytest
from conftest import (
    FIXTURE,
    godot_command,
    needs_gamescope,
    needs_godot,
    wait_for_scene,
)
from PIL import Image

from eyesol8.adapter import AdapterError
from eyesol8.session import Session

ADDON = FIXTURE.parent.parent.parent / "adapters" / "godot" / "addons" / "eyesol8_adapter"


def test_fixture_uses_the_shipped_addon():
    installed = FIXTURE / "addons" / "eyesol8_adapter"
    assert installed.resolve() == ADDON.resolve()


@pytest.fixture
def godot(no_leaks):
    s = Session.start(godot_command(), cwd=FIXTURE, width=640, height=360)
    try:
        wait_for_scene(s)
        yield s
    finally:
        s.stop()


@needs_gamescope
@needs_godot
def test_tree_contains_main(godot):
    nodes = godot.query("tree", {"details": True})
    main = next(n for n in nodes if n["path"] == "/root/Main")
    assert main["type"] == "Node2D" and main["visible"] is True
    assert not any("Eyesol8Adapter" in n["path"] for n in nodes)
    assert godot.query("tree", {"root": "/root/Main", "max_depth": 0}) == [
        {"path": "/root/Main", "name": "Main", "type": "Node2D"}
    ]


@needs_gamescope
@needs_godot
def test_engine_screenshot(godot):
    img = Image.open(io.BytesIO(godot.screenshot(source="engine"))).convert("RGB")
    assert img.size == (640, 360)
    assert img.getpixel((5, 5)) == (255, 0, 0)
    small = Image.open(io.BytesIO(godot.screenshot(source="engine", scale=0.5)))
    assert small.size == (320, 180)


@needs_gamescope
@needs_godot
def test_action_frame_wait_time_scale(godot):
    _, cursor = godot.logs()
    assert godot.query("action", {"name": "ui_accept", "frames": 2}) is True
    godot.wait_for_log(r"ACTION ui_accept", timeout=5, since=cursor)

    before = godot.query("frame_wait", {"n": 1})
    assert godot.query("frame_wait", {"n": 3}) >= before + 3

    assert godot.query("time_scale", {"value": 0.5}) == 1.0
    assert godot.query("time_scale") == 0.5

    with pytest.raises(AdapterError, match="unknown action"):
        godot.query("action", {"name": "no_such_action"})
    with pytest.raises(AdapterError, match="unknown cmd"):
        godot.query("explode")


@needs_gamescope
@needs_godot
def test_wrong_token_is_rejected(godot):
    from eyesol8.adapter import AdapterClient

    intruder = AdapterClient(godot.adapter.port, "wrong")
    try:
        with pytest.raises(AdapterError):
            intruder.request("tree", timeout=5)
    finally:
        intruder.close()
    assert godot.query("tree", {"max_depth": 0})


@needs_gamescope
def test_query_without_adapter_fails_clearly(no_leaks):
    s = Session.start(["sleep", "60"])
    try:
        with pytest.raises(AdapterError, match="no engine adapter"):
            s.query("tree", timeout=1)
    finally:
        s.stop()
    assert os.environ.get("EYESOL8_ADAPTER_PORT") is None
