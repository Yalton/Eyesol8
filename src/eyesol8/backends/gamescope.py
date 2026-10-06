from __future__ import annotations

import subprocess
from pathlib import Path

from eyesol8 import capture
from eyesol8.backends.base import Backend, require


class GamescopeBackend(Backend):
    """``gamescope --backend headless`` with one Xwayland; needs a GPU."""

    name = "gamescope"
    required_env = ("DISPLAY", "GAMESCOPE_WAYLAND_DISPLAY")

    def launch(self, shim_argv, *, cwd, env, width, height, session_dir: Path) -> None:
        gamescope = require("gamescope")
        size = [str(width), str(height)]
        argv = [
            gamescope, "--backend", "headless", "--xwayland-count", "1",
            "-W", size[0], "-H", size[1], "-w", size[0], "-h", size[1],
            "--", *shim_argv,
        ]  # fmt: skip
        self.log_path = session_dir / "gamescope.log"
        # Gamescope keeps its own copy of the log fd; ours can close right away.
        with open(self.log_path, "wb") as log:
            self.procs = [
                subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            ]

    def screenshot(self, values, timeout):
        return capture.gamescope_png(values["GAMESCOPE_WAYLAND_DISPLAY"], timeout=timeout)
