from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path


class MissingDependencyError(RuntimeError):
    pass


class BackendError(RuntimeError):
    pass


def require(binary: str, package_hint: str = "") -> str:
    path = shutil.which(binary)
    if path is None:
        hint = f" (install {package_hint})" if package_hint else ""
        raise MissingDependencyError(
            f"{binary} not found on PATH{hint}; Eyesol8 needs it to run programs headlessly"
        )
    return path


class Backend(ABC):
    """One display server for one session.

    ``launch`` must leave every process it starts in the process group of
    ``procs[0]`` so the session can kill them all at once.
    """

    name: str
    # Shim-reported variables that must be non-empty once the program starts.
    required_env: tuple[str, ...] = ("DISPLAY",)

    def __init__(self) -> None:
        self.procs: list[subprocess.Popen] = []
        self.log_path: Path | None = None

    @abstractmethod
    def launch(
        self,
        shim_argv: list[str],
        *,
        cwd,
        env: dict[str, str],
        width: int,
        height: int,
        session_dir: Path,
    ) -> None:
        """Start the display server and run ``shim_argv`` inside it."""

    @property
    def main(self) -> subprocess.Popen:
        """The process whose exit means the session is over."""
        return self.procs[0]

    @property
    def pgid(self) -> int:
        return self.procs[0].pid

    def alive(self) -> bool:
        return bool(self.procs) and self.main.poll() is None

    @abstractmethod
    def screenshot(self, values: dict[str, str], timeout: float) -> bytes:
        """Return a PNG of the whole display. ``values`` is the shim's env report."""

    def wait_for_frame(self, values: dict[str, str], timeout: float) -> bytes:
        return self.screenshot(values, timeout)

    def log_tail(self, n: int = 20) -> str:
        if self.log_path is None:
            return ""
        try:
            text = self.log_path.read_text(errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-n:])
