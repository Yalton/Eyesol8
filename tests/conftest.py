import io
import shutil
import time
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "godot_scene"

needs_gamescope = pytest.mark.skipif(
    shutil.which("gamescope") is None, reason="gamescope not on PATH"
)
needs_godot = pytest.mark.skipif(shutil.which("godot") is None, reason="godot not on PATH")


# Measured 1.7-2.4s on an RTX 2070, warm or cold caches; doubled for slack.
SCENE_READY_SECONDS = 5.0


def godot_command() -> list[str]:
    return ["godot", "--audio-driver", "Dummy", "--path", str(FIXTURE)]


needs_xvfb = pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not on PATH")


def session_processes() -> dict[int, str]:
    """PIDs of this user's processes started by any Eyesol8 session.

    Matched by the EYESOL8_SESSION_ID env var rather than by name, because
    other projects on the same machine run their own gamescope/godot/Xvfb.
    """
    found = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if b"EYESOL8_SESSION_ID=" in (proc / "environ").read_bytes():
                found[int(proc.name)] = (proc / "comm").read_text().strip()
        except OSError:
            continue
    return found


@pytest.fixture
def no_leaks():
    before = set(session_processes())
    yield
    deadline = time.monotonic() + 5
    while True:
        leaked = {pid: name for pid, name in session_processes().items() if pid not in before}
        if not leaked or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert not leaked, f"processes left behind: {leaked}"


def screenshot_rgb(session):
    from PIL import Image

    return Image.open(io.BytesIO(session.screenshot())).convert("RGB")


def wait_for_scene(session, color=(255, 0, 0), timeout=SCENE_READY_SECONDS):
    """Wait until the fixture scene is drawn (pixel (5, 5) has ``color``).

    Godot shows a grey boot splash (36, 36, 36) for about 2s after start, and
    input sent before the scene appears is lost.
    """
    deadline = time.monotonic() + timeout
    while True:
        img = screenshot_rgb(session)
        if img.getpixel((5, 5)) == color:
            return img
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"scene not drawn within {timeout}s; pixel (5, 5) = {img.getpixel((5, 5))}"
            )
        time.sleep(0.25)
