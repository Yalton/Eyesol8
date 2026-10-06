"""Run one program inside a private headless display.

Each Session owns one display server from eyesol8.backends (Gamescope by
default, or Xvfb). A small shell shim runs inside it, records the DISPLAY (and
GAMESCOPE_WAYLAND_DISPLAY) it was given, then runs the program with its output
sent to a FIFO that this module reads into a ring buffer and a log file.
"""

from __future__ import annotations

import collections
import os
import re
import shutil
import signal
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from eyesol8 import capture
from eyesol8.adapter import LISTENING_RE, PORT_ENV, TOKEN_ENV, AdapterClient, new_token
from eyesol8.backends import Backend, MissingDependencyError, get_backend
from eyesol8.input import InputDevice

DEFAULT_LOG_LINES = 10_000
DEFAULT_IDLE_TIMEOUT = 30 * 60
ENV_FILE_TIMEOUT = 10.0

# Runs inside the display server. "$1" is the session dir, the rest is the program.
_SHIM = r"""
dir="$1"; shift
"$@" > "$dir/output" 2>&1 &
child=$!
printf 'DISPLAY=%s\nGAMESCOPE_WAYLAND_DISPLAY=%s\nPID=%s\n' \
    "$DISPLAY" "$GAMESCOPE_WAYLAND_DISPLAY" "$child" > "$dir/env.tmp"
mv "$dir/env.tmp" "$dir/env"
wait "$child"
echo $? > "$dir/exit"
"""

# Values that point audio clients at nothing, so the program stays silent.
_MUTED_AUDIO_ENV = {
    "PULSE_SERVER": "unix:/nonexistent/eyesol8-muted",
    "PIPEWIRE_REMOTE": "eyesol8-muted-nonexistent",
}


__all__ = [
    "MissingDependencyError",
    "Session",
    "SessionRegistry",
    "SessionStartError",
    "StopResult",
]

# Set for every process a session starts, so leaks can be traced to a session.
SESSION_ID_ENV = "EYESOL8_SESSION_ID"


class SessionStartError(RuntimeError):
    pass


@dataclass
class StopResult:
    exit_code: int | None
    last_lines: list[str]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); treat it as gone.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except (OSError, IndexError):
        return False


class Session:
    id: str
    display: str
    wayland_display: str
    backend: Backend
    session_dir: Path
    log_path: Path

    @classmethod
    def start(
        cls,
        command: list[str],
        cwd: str | os.PathLike | None = None,
        env: dict[str, str] | None = None,
        width: int = 1280,
        height: int = 720,
        mute_audio: bool = True,
        log_lines: int = DEFAULT_LOG_LINES,
        backend: str = "gamescope",
    ) -> Session:
        if not command:
            raise ValueError("command must not be empty")
        display_backend = get_backend(backend)

        self = cls.__new__(cls)
        self.id = uuid.uuid4().hex[:12]
        self.command = list(command)
        self.backend = display_backend
        self.width = width
        self.height = height
        self.started_at = time.monotonic()
        self.session_dir = Path(tempfile.mkdtemp(prefix=f"eyesol8-{self.id}-"))
        self.log_path = self.session_dir / "program.log"
        self.last_activity = time.monotonic()
        self._child_pid: int | None = None
        self._exit_code: int | None = None
        self._stopped = False
        self._input_device: InputDevice | None = None
        self._input_lock = threading.Lock()
        self._init_log_buffer(log_lines)

        child_env = dict(os.environ)
        for key in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET", "DISPLAY"):
            child_env.pop(key, None)
        if mute_audio:
            child_env.update(_MUTED_AUDIO_ENV)
        self.adapter = AdapterClient(new_token(), find_port=self._wait_adapter_port)
        # The adapter binds port 0 and prints the port it got; see _append_log.
        child_env[PORT_ENV] = "0"
        child_env[TOKEN_ENV] = self.adapter.token
        if env:
            child_env.update(env)
        child_env[SESSION_ID_ENV] = self.id

        fifo = self.session_dir / "output"
        os.mkfifo(fifo)
        self._reader = threading.Thread(target=self._read_output, args=(fifo,), daemon=True)
        self._reader.start()

        shim_argv = ["sh", "-c", _SHIM, "eyesol8-shim", str(self.session_dir), *command]
        try:
            display_backend.launch(
                shim_argv,
                cwd=cwd,
                env=child_env,
                width=width,
                height=height,
                session_dir=self.session_dir,
            )
        except Exception:
            if display_backend.procs:
                self._kill_backend()
            self._unblock_reader()
            self._reader.join(timeout=2)
            shutil.rmtree(self.session_dir, ignore_errors=True)
            raise

        try:
            values = self._read_env_file(ENV_FILE_TIMEOUT)
        except Exception:
            self.stop(grace_seconds=0)
            raise
        self._display_values = values
        self.display = values["DISPLAY"]
        self.wayland_display = values.get("GAMESCOPE_WAYLAND_DISPLAY", "")
        self._child_pid = int(values["PID"])
        missing = [k for k in display_backend.required_env if not values.get(k)]
        if missing:
            self.stop(grace_seconds=0)
            raise SessionStartError(f"{backend} did not provide {', '.join(missing)}: {values}")
        return self

    def _read_env_file(self, timeout: float) -> dict[str, str]:
        path = self.session_dir / "env"
        deadline = time.monotonic() + timeout
        while not path.exists():
            if not self.backend.alive():
                raise SessionStartError(
                    f"{self.backend.name} exited before the program started:\n"
                    f"{self.backend.log_tail()}"
                )
            if time.monotonic() > deadline:
                raise SessionStartError(
                    f"{self.backend.name} did not report its display within {timeout}s:\n"
                    f"{self.backend.log_tail()}"
                )
            time.sleep(0.05)
        values = {}
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            values[key] = value
        return values

    # --- logs -------------------------------------------------------------

    def _init_log_buffer(self, max_lines: int) -> None:
        self._log_lines: collections.deque[tuple[int, str]] = collections.deque(maxlen=max_lines)
        self._log_cursor = 0
        self._log_cond = threading.Condition()
        self._log_file = None
        self._adapter_port: int | None = None

    def _append_log(self, line: str) -> None:
        with self._log_cond:
            self._log_cursor += 1
            self._log_lines.append((self._log_cursor, line))
            # Caught here rather than searched for later, so a chatty program
            # cannot push the line out of the ring buffer first.
            if self._adapter_port is None and line.startswith("EYESOL8_ADAPTER"):
                match = LISTENING_RE.match(line)
                if match:
                    self._adapter_port = int(match.group(1))
            self._log_cond.notify_all()

    def _wait_adapter_port(self, timeout: float) -> int:
        with self._log_cond:
            if not self._log_cond.wait_for(lambda: self._adapter_port is not None, timeout):
                raise TimeoutError
            return self._adapter_port

    def _read_output(self, fifo: Path) -> None:
        # Blocks until the shim opens the FIFO for writing; ends at EOF when
        # the program (and anything it spawned) closes its output.
        with open(fifo, "rb") as src, open(self.log_path, "wb") as dst:
            for raw in src:
                dst.write(raw)
                dst.flush()
                self._append_log(raw.decode(errors="replace").rstrip("\r\n"))

    def logs(
        self, tail: int | None = None, grep: str | None = None, since: int | None = None
    ) -> tuple[list[str], int]:
        """Return buffered lines and the cursor of the newest line.

        Pass the returned cursor back as ``since`` to get only newer lines.
        """
        pattern = re.compile(grep) if grep else None
        with self._log_cond:
            entries = list(self._log_lines)
            cursor = self._log_cursor
        lines = [
            line
            for n, line in entries
            if (since is None or n > since) and (pattern is None or pattern.search(line))
        ]
        if tail is not None:
            lines = lines[-tail:] if tail > 0 else []
        return lines, cursor

    def wait_for_log(self, regex: str, timeout: float = 10.0, since: int | None = None) -> str:
        """Wait for a buffered line matching ``regex`` and return it."""
        pattern = re.compile(regex)
        deadline = time.monotonic() + timeout
        seen = since or 0
        with self._log_cond:
            while True:
                for n, line in self._log_lines:
                    if n > seen and pattern.search(line):
                        return line
                if self._log_lines:
                    seen = self._log_lines[-1][0]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no log line matched {regex!r} within {timeout}s")
                self._log_cond.wait(min(remaining, 0.5))

    # --- capture and input -------------------------------------------------

    def screenshot(
        self,
        region: tuple[int, int, int, int] | None = None,
        scale: float | None = None,
        save_to: str | os.PathLike | None = None,
        timeout: float = capture.FILE_APPEAR_TIMEOUT,
        source: str = "compositor",
    ) -> bytes:
        """Return a PNG of the session's display.

        ``region`` is (x, y, w, h) in display pixels and is applied before
        ``scale`` in (0, 1]; ``save_to`` also writes the PNG there.
        ``source="engine"`` asks the in-process engine adapter for the game's
        own viewport instead of the composited display.
        """
        self.touch()
        if source == "engine":
            png = self.adapter.screenshot(timeout=timeout)
            return capture.postprocess(png, region=region, scale=scale, save_to=save_to)
        if source != "compositor":
            raise ValueError(f"source must be 'compositor' or 'engine', got {source!r}")
        png = self.backend.screenshot(self._display_values, timeout)
        return capture.postprocess(png, region=region, scale=scale, save_to=save_to)

    def query(self, cmd: str, args: dict | None = None, timeout: float = 10.0):
        """Send one command to the engine adapter and return its result."""
        self.touch()
        return self.adapter.request(cmd, args, timeout=timeout)

    def input(self, actions: list[dict]) -> int:
        """Apply input actions to the session; see eyesol8.input.InputDevice.apply."""
        self.touch()
        with self._input_lock:
            if self._input_device is None:
                self._input_device = InputDevice(self.display)
            return self._input_device.apply(actions)

    # --- lifecycle --------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """PID of the program inside the display server."""
        return self._child_pid

    @property
    def running(self) -> bool:
        return not self._stopped and self.backend.alive()

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def wait_for_frame(self, timeout: float = capture.FILE_APPEAR_TIMEOUT) -> bytes:
        """Wait until the program has drawn its first frame; return it as a PNG.

        This means a window is up, not that the program is ready for input:
        a game may still be showing a splash screen.
        """
        # Gamescope queues the request until it composites a frame; Xvfb
        # answers at once, so there it can return a blank frame.
        self.touch()
        return self.backend.wait_for_frame(self._display_values, timeout)

    def stop(self, grace_seconds: float = 3.0) -> StopResult:
        """Stop the program and its display server; return the exit code and last log lines."""
        if not self._stopped:
            self._stopped = True
            with self._input_lock:
                if self._input_device is not None:
                    self._input_device.close()
                    self._input_device = None
            self.adapter.close()
            self._terminate(grace_seconds)
            self._reader.join(timeout=2)
            exit_file = self.session_dir / "exit"
            if exit_file.exists():
                self._exit_code = int(exit_file.read_text().strip() or -1)
            shutil.rmtree(self.session_dir, ignore_errors=True)
        lines, _ = self.logs(tail=50)
        return StopResult(exit_code=self._exit_code, last_lines=lines)

    def _terminate(self, grace_seconds: float) -> None:
        pid = self._child_pid
        if pid is not None and _alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + grace_seconds
            while _alive(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
        # Let the shim record the exit code before tearing everything down.
        deadline = time.monotonic() + 0.5
        while (
            pid is not None
            and not (self.session_dir / "exit").exists()
            and self.backend.main.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        self._kill_backend()
        if pid is not None and _alive(pid):
            # The program may have left the display server's process group.
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._unblock_reader()

    def _kill_backend(self) -> None:
        try:
            os.killpg(self.backend.pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Display servers can outlive their child; kill each one explicitly.
        for proc in self.backend.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)

    def _unblock_reader(self) -> None:
        # If the shim never opened the FIFO, the reader is stuck in open().
        if not self._reader.is_alive():
            return
        try:
            fd = os.open(self.session_dir / "output", os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError:
            pass


class SessionRegistry:
    """Tracks live sessions, stops them all on shutdown, reaps idle ones."""

    def __init__(self, idle_timeout: float = DEFAULT_IDLE_TIMEOUT) -> None:
        self.idle_timeout = idle_timeout
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._reaper: threading.Thread | None = None
        self._closing = threading.Event()

    def start(self, command: list[str], **kwargs) -> Session:
        session = Session.start(command, **kwargs)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"no session {session_id!r}")
        return session

    def list(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def touch(self, session_id: str) -> None:
        self.get(session_id).touch()

    def stop(self, session_id: str, grace_seconds: float = 3.0) -> StopResult:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise KeyError(f"no session {session_id!r}")
        return session.stop(grace_seconds)

    def reap_idle(self) -> list[str]:
        now = time.monotonic()
        idle = [s.id for s in self.list() if now - s.last_activity > self.idle_timeout]
        for session_id in idle:
            try:
                self.stop(session_id)
            except KeyError:
                pass
        return idle

    def start_reaper(self, interval: float = 60.0) -> None:
        if self._reaper is not None:
            return

        def loop() -> None:
            while not self._closing.wait(interval):
                self.reap_idle()

        self._reaper = threading.Thread(target=loop, daemon=True)
        self._reaper.start()

    def shutdown(self) -> None:
        self._closing.set()
        for session in self.list():
            try:
                self.stop(session.id)
            except KeyError:
                pass
