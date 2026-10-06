from __future__ import annotations

import io
import os
import select
import subprocess
import time
from pathlib import Path

from PIL import Image
from Xlib import X, display

from eyesol8.backends.base import Backend, BackendError, require

DISPLAYFD_TIMEOUT = 10.0


class XvfbBackend(Backend):
    """Xvfb: a framebuffer X server with no GPU and no compositor.

    Programs render in software (Mesa llvmpipe); a Godot game needs
    ``--rendering-driver opengl3``. Screenshots read the root window with
    XGetImage, which works here because nothing composites behind X's back.
    """

    name = "xvfb"

    def launch(self, shim_argv, *, cwd, env, width, height, session_dir: Path) -> None:
        xvfb = require("Xvfb", "xorg-x11-server-Xvfb or xvfb")
        self.log_path = session_dir / "xvfb.log"
        read_fd, write_fd = os.pipe()
        argv = [
            xvfb, "-displayfd", str(write_fd),
            "-screen", "0", f"{width}x{height}x24",
            "-nolisten", "tcp",
        ]  # fmt: skip
        try:
            with open(self.log_path, "wb") as log:
                # Own process group in this session, so the shim may join it
                # (a process cannot join a group in another session).
                server = subprocess.Popen(
                    argv,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    pass_fds=(write_fd,),
                    process_group=0,
                )
            self.procs = [server]
            os.close(write_fd)
            write_fd = -1
            number = self._read_display_number(read_fd, server)
        finally:
            if write_fd != -1:
                os.close(write_fd)
            os.close(read_fd)
        with open(self.log_path, "ab") as log:
            shim = subprocess.Popen(
                shim_argv,
                cwd=cwd,
                env={**env, "DISPLAY": f":{number}"},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                process_group=server.pid,
            )
        self.procs.append(shim)

    @property
    def main(self) -> subprocess.Popen:
        # Xvfb outlives the program; the session ends with the shim.
        return self.procs[-1]

    def alive(self) -> bool:
        return len(self.procs) == 2 and all(p.poll() is None for p in self.procs)

    def _read_display_number(self, fd: int, server: subprocess.Popen) -> str:
        deadline = time.monotonic() + DISPLAYFD_TIMEOUT
        data = b""
        while not data.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            if server.poll() is not None or remaining <= 0:
                raise BackendError(
                    f"Xvfb did not start a display (exit code {server.poll()}):\n{self.log_tail()}"
                )
            ready, _, _ = select.select([fd], [], [], min(remaining, 0.2))
            if ready:
                chunk = os.read(fd, 32)
                if not chunk:
                    continue
                data += chunk
        return data.decode().strip()

    def screenshot(self, values, timeout):
        conn = display.Display(values["DISPLAY"])
        try:
            root = conn.screen().root
            geometry = root.get_geometry()
            raw = root.get_image(0, 0, geometry.width, geometry.height, X.ZPixmap, 0xFFFFFFFF).data
        finally:
            conn.close()
        image = Image.frombytes("RGB", (geometry.width, geometry.height), raw, "raw", "BGRX")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
