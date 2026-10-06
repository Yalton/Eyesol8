"""Screenshot helpers: Gamescope capture and shared crop/scale.

Under Gamescope, X11 grabs (ffmpeg x11grab, XGetImage) return black frames
because Gamescope composites client buffers itself, so gamescope_png asks
Gamescope. Other backends capture in eyesol8.backends.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

# Gamescope saves a screenshot only after it has composited a frame, i.e. once
# the program has mapped and drawn a window; earlier requests are queued, not
# dropped. A program with no window never gets a screenshot.
FILE_APPEAR_TIMEOUT = 20.0
FILE_STABLE_TIMEOUT = 5.0


class CaptureError(RuntimeError):
    pass


def gamescope_png(wayland_display: str, timeout: float = FILE_APPEAR_TIMEOUT) -> bytes:
    """Return a full-size PNG of the Gamescope display named ``wayland_display``."""
    gamescopectl = shutil.which("gamescopectl")
    if gamescopectl is None:
        raise CaptureError("gamescopectl not found on PATH; it ships with gamescope")

    with tempfile.TemporaryDirectory(prefix="eyesol8-shot-") as tmp:
        path = Path(tmp) / "shot.png"
        env = {**os.environ, "GAMESCOPE_WAYLAND_DISPLAY": wayland_display}
        result = subprocess.run(
            [gamescopectl, "screenshot", str(path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise CaptureError(
                f"gamescopectl screenshot failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()}"
            )
        _wait_for_complete_file(path, timeout)
        return path.read_bytes()


def postprocess(
    png: bytes,
    region: tuple[int, int, int, int] | None = None,
    scale: float | None = None,
    save_to: str | os.PathLike | None = None,
) -> bytes:
    """Crop to ``region``, shrink by ``scale``, optionally save; return a PNG."""
    if scale is not None and not 0 < scale <= 1:
        raise ValueError(f"scale must be in (0, 1], got {scale}")
    image = Image.open(io.BytesIO(png))
    image.load()
    if region is not None:
        x, y, w, h = region
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > image.width or y + h > image.height:
            raise ValueError(f"region {region} is outside the {image.width}x{image.height} image")
        image = image.crop((x, y, x + w, y + h))
    if scale is not None and scale != 1:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    data = buf.getvalue()
    if save_to is not None:
        Path(save_to).write_bytes(data)
    return data


def _wait_for_complete_file(path: Path, timeout: float) -> None:
    # gamescopectl returns before Gamescope writes the file.
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise CaptureError(
                f"gamescope did not write a screenshot within {timeout}s; "
                "has the program shown a window yet?"
            )
        time.sleep(0.02)
    deadline = time.monotonic() + FILE_STABLE_TIMEOUT
    last = -1
    while True:
        size = path.stat().st_size
        if size > 0 and size == last:
            return
        if time.monotonic() > deadline:
            raise CaptureError(f"screenshot file did not settle within {FILE_STABLE_TIMEOUT}s")
        last = size
        time.sleep(0.05)
