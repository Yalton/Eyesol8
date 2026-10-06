from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from Xlib import XK

from eyesol8.backends.base import Backend, BackendError, require
from eyesol8.backends.wayland_wire import BTN_LEFT, BTN_MIDDLE, BTN_RIGHT, VirtualInput

# Make toolkits pick Wayland; this backend exists for apps that need it.
_WAYLAND_TOOLKIT_ENV = {
    "GDK_BACKEND": "wayland",
    "QT_QPA_PLATFORM": "wayland",
    "SDL_VIDEODRIVER": "wayland",
    "MOZ_ENABLE_WAYLAND": "1",
}
TOOL_TIMEOUT = 10.0


class WlrootsBackend(Backend):
    """cage (a wlroots kiosk compositor) on the headless wlroots backend.

    For Wayland-native apps (GTK4, Qt 6) that will not run under Xwayland.
    Screenshots use grim (wlr-screencopy), keys use wtype, and the mouse uses
    a built-in virtual pointer client (see wayland_wire). The output is cage's
    default headless size, 1280x720; cage stretches the app to fill it.
    """

    name = "wlroots"
    required_env = ("WAYLAND_DISPLAY",)
    width = 1280
    height = 720

    def launch(self, shim_argv, *, cwd, env, width, height, session_dir: Path) -> None:
        cage = require("cage")
        for tool in ("grim", "wtype"):
            require(tool)
        if (width, height) != (self.width, self.height):
            raise ValueError(
                f"the wlroots backend only supports {self.width}x{self.height}, "
                f"got {width}x{height}"
            )
        # A private runtime dir keeps cage's socket away from the host's
        # wayland-0. Unix socket paths must stay under 108 bytes.
        self.runtime_dir = session_dir / "run"
        self.runtime_dir.mkdir(mode=0o700)
        if len(str(self.runtime_dir / "wayland-0.lock")) >= 108:
            raise BackendError(f"session dir path too long for a Wayland socket: {session_dir}")
        self.log_path = session_dir / "cage.log"
        child_env = {
            **_WAYLAND_TOOLKIT_ENV,
            **env,
            "XDG_RUNTIME_DIR": str(self.runtime_dir),
            "WLR_BACKENDS": "headless",
            "WLR_LIBINPUT_NO_DEVICES": "1",
        }
        with open(self.log_path, "wb") as log:
            self.procs = [
                subprocess.Popen(
                    [cage, "-d", "--", *shim_argv],
                    cwd=cwd,
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            ]

    def _tool_env(self, values: dict[str, str]) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "XDG_RUNTIME_DIR": str(self.runtime_dir),
            "WAYLAND_DISPLAY": values["WAYLAND_DISPLAY"],
        }

    def run_tool(self, values: dict[str, str], argv: list[str]) -> bytes:
        result = subprocess.run(
            argv,
            env=self._tool_env(values),
            capture_output=True,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            raise BackendError(
                f"{argv[0]} failed ({result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        return result.stdout

    def screenshot(self, values, timeout):
        return self.run_tool(values, ["grim", "-t", "png", "-"])

    def input_device(self, values: dict[str, str]) -> WaylandInput:
        return WaylandInput(self, values)


_BUTTONS = {1: BTN_LEFT, 2: BTN_MIDDLE, 3: BTN_RIGHT}
DRAG_STEPS = 10
PRESS_DELAY = 0.03
_MODIFIERS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "super": "logo",
    "meta": "logo",
    "logo": "logo",
}
_KEY_ALIASES = {
    "enter": "Return",
    "esc": "Escape",
    "del": "Delete",
    "backspace": "BackSpace",
    "pageup": "Prior",
    "pagedown": "Next",
}
_ACTIONS = ("key", "text", "move", "click", "drag", "scroll", "wait")


class WaylandInput:
    """The input action language of eyesol8.input for a wlroots session.

    Keys and text go through wtype; the mouse goes through one virtual
    pointer held open for the session, so the app keeps pointer focus.
    """

    def __init__(self, backend: WlrootsBackend, values: dict[str, str]) -> None:
        self._backend = backend
        self._values = values
        self._virtual = VirtualInput(backend.runtime_dir, values["WAYLAND_DISPLAY"])

    def close(self) -> None:
        self._virtual.close()

    def apply(self, actions: list[dict]) -> int:
        """Apply actions in order; a bad action list sends nothing."""
        steps = [self._plan(action) for action in actions]
        for step in steps:
            step()
        self._virtual.sync()
        return len(steps)

    def _run(self, *argv: str) -> None:
        self._backend.run_tool(self._values, list(argv))

    def _plan(self, action):
        if not isinstance(action, dict):
            raise TypeError(f"action must be an object, got {action!r}")
        kinds = [k for k in _ACTIONS if k in action]
        if len(kinds) != 1:
            raise ValueError(f"action needs exactly one of {', '.join(_ACTIONS)}: {action!r}")
        kind = kinds[0]
        value = action[kind]
        button = int(action.get("button", 1))

        if kind == "key":
            names = str(value).split("+")
            if any(not n for n in names):
                raise ValueError(f"malformed key combo {value!r}")
            mods = []
            for name in names[:-1]:
                if name.lower() not in _MODIFIERS:
                    raise ValueError(f"unknown modifier {name!r}")
                mods.append(_MODIFIERS[name.lower()])
            key = _keysym_name(names[-1])
            argv = ["wtype"]
            for mod in mods:
                argv += ["-M", mod]
            argv += ["-k", key]
            for mod in reversed(mods):
                argv += ["-m", mod]
            return lambda: self._run(*argv)
        if kind == "text":
            text = str(value)
            return lambda: self._run("wtype", "--", text) if text else None
        if kind == "move":
            x, y = _point(value)
            return lambda: self._move(x, y)
        if kind == "click":
            x, y = _point(value)
            code = _button(button)
            return lambda: (self._move(x, y), self._press(code, True), self._press(code, False))
        if kind == "drag":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(f"drag needs [[x1, y1], [x2, y2]], got {value!r}")
            start, end = _point(value[0]), _point(value[1])
            code = _button(button)
            return lambda: self._drag(start, end, code)
        if kind == "scroll":
            dy = int(value)
            return lambda: self._virtual.scroll(dy)
        ms = float(value)
        if ms < 0:
            raise ValueError(f"wait must be >= 0 ms, got {value!r}")
        return lambda: time.sleep(ms / 1000)

    def _move(self, x: int, y: int) -> None:
        backend = self._backend
        self._virtual.move(x, y, backend.width, backend.height)
        self._virtual.sync()
        time.sleep(PRESS_DELAY)

    def _press(self, code: int, pressed: bool) -> None:
        self._virtual.button(code, pressed)
        self._virtual.sync()
        time.sleep(PRESS_DELAY)

    def _drag(self, start: tuple[int, int], end: tuple[int, int], code: int) -> None:
        self._move(*start)
        self._press(code, True)
        for i in range(1, DRAG_STEPS + 1):
            self._move(
                round(start[0] + (end[0] - start[0]) * i / DRAG_STEPS),
                round(start[1] + (end[1] - start[1]) * i / DRAG_STEPS),
            )
        self._press(code, False)


def _keysym_name(name: str) -> str:
    name = _KEY_ALIASES.get(name.lower(), name)
    if XK.string_to_keysym(name) == 0:
        capitalized = name.capitalize()
        if XK.string_to_keysym(capitalized) == 0:
            raise ValueError(f"unknown key {name!r}")
        name = capitalized
    return name


def _button(button: int) -> int:
    if button not in _BUTTONS:
        raise ValueError(f"button must be 1, 2 or 3 on the wlroots backend, got {button}")
    return _BUTTONS[button]


def _point(value) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"expected [x, y], got {value!r}")
    x, y = int(value[0]), int(value[1])
    if x < 0 or y < 0:
        raise ValueError(f"coordinates must be >= 0, got {value!r}")
    return x, y
