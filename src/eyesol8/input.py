"""Keyboard and mouse input into a session via XTEST on its Xwayland DISPLAY."""

from __future__ import annotations

import time

from Xlib import XK, X, display
from Xlib.ext import xtest

# Pause between press and release so per-frame event polling sees both.
PRESS_DELAY = 0.03
DRAG_STEPS = 10

_MODIFIERS = {
    "ctrl": "Control_L",
    "control": "Control_L",
    "shift": "Shift_L",
    "alt": "Alt_L",
    "super": "Super_L",
    "meta": "Super_L",
}

# Friendly names that XK.string_to_keysym does not know.
_KEY_ALIASES = {
    "enter": "Return",
    "esc": "Escape",
    "del": "Delete",
    "backspace": "BackSpace",
    "pageup": "Prior",
    "pagedown": "Next",
    " ": "space",
    "\n": "Return",
    "\t": "Tab",
}

_ACTIONS = ("key", "text", "move", "click", "drag", "scroll", "wait")


class InputDevice:
    """One XTEST connection to a session's display."""

    def __init__(self, display_name: str) -> None:
        self._display = display.Display(display_name)
        if not self._display.has_extension("XTEST"):
            self._display.close()
            raise RuntimeError(f"display {display_name} has no XTEST extension")

    def close(self) -> None:
        self._display.close()

    def apply(self, actions: list[dict]) -> int:
        """Apply actions in order and return how many were applied.

        Every action is validated before any is sent, so a bad action list
        sends nothing. Actions: {key: 'ctrl+s'}, {text: 'hi'}, {move: [x, y]},
        {click: [x, y], button: 1}, {drag: [[x1, y1], [x2, y2]], button: 1},
        {scroll: dy} (positive scrolls down), {wait: ms}.
        """
        steps = [self._plan(action) for action in actions]
        for step in steps:
            step()
        self._display.sync()
        return len(steps)

    # --- validation -------------------------------------------------------

    def _plan(self, action: dict):
        if not isinstance(action, dict):
            raise ValueError(f"action must be an object, got {action!r}")
        kinds = [k for k in _ACTIONS if k in action]
        if len(kinds) != 1:
            raise ValueError(f"action needs exactly one of {', '.join(_ACTIONS)}: {action!r}")
        kind = kinds[0]
        value = action[kind]
        button = int(action.get("button", 1))

        if kind == "key":
            names = [n for n in str(value).split("+")]
            if any(not n for n in names):
                raise ValueError(f"malformed key combo {value!r}")
            mods = [self._keycode(_MODIFIERS.get(n.lower(), n)) for n in names[:-1]]
            key = self._keycode(names[-1])
            return lambda: self._key_combo(mods, key)
        if kind == "text":
            strokes = [self._char_stroke(c) for c in str(value)]
            return lambda: [self._key_combo(mods, key) for mods, key in strokes]
        if kind == "move":
            x, y = _point(value)
            return lambda: self._move(x, y)
        if kind == "click":
            x, y = _point(value)
            return lambda: (self._move(x, y), self._button(button))
        if kind == "drag":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(f"drag needs [[x1, y1], [x2, y2]], got {value!r}")
            start, end = _point(value[0]), _point(value[1])
            return lambda: self._drag(start, end, button)
        if kind == "scroll":
            dy = int(value)
            return lambda: [self._button(5 if dy > 0 else 4) for _ in range(abs(dy))]
        ms = float(value)
        if ms < 0:
            raise ValueError(f"wait must be >= 0 ms, got {value!r}")
        return lambda: time.sleep(ms / 1000)

    def _keycode(self, name: str) -> int:
        name = _KEY_ALIASES.get(name.lower(), name)
        keysym = XK.string_to_keysym(name)
        if keysym == X.NoSymbol and len(name) > 1:
            keysym = XK.string_to_keysym(name.capitalize())
        if keysym == X.NoSymbol:
            raise ValueError(f"unknown key {name!r}")
        keycode = self._display.keysym_to_keycode(keysym)
        if keycode == 0:
            raise ValueError(f"key {name!r} is not on the session's keyboard map")
        return keycode

    def _char_stroke(self, char: str) -> tuple[list[int], int]:
        if char in _KEY_ALIASES:
            return [], self._keycode(_KEY_ALIASES[char])
        code = ord(char)
        keysym = code if code < 0x100 else 0x01000000 + code
        for keycode, index in self._display.keysym_to_keycodes(keysym):
            if index == 0:
                return [], keycode
            if index == 1:
                return [self._keycode("Shift_L")], keycode
        raise ValueError(f"character {char!r} cannot be typed on the session's keyboard map")

    # --- sending ----------------------------------------------------------

    def _key_combo(self, mods: list[int], key: int) -> None:
        d = self._display
        for mod in mods:
            xtest.fake_input(d, X.KeyPress, mod)
        xtest.fake_input(d, X.KeyPress, key)
        d.sync()
        time.sleep(PRESS_DELAY)
        xtest.fake_input(d, X.KeyRelease, key)
        for mod in reversed(mods):
            xtest.fake_input(d, X.KeyRelease, mod)
        d.sync()
        time.sleep(PRESS_DELAY)

    def _move(self, x: int, y: int) -> None:
        xtest.fake_input(self._display, X.MotionNotify, x=x, y=y)
        self._display.sync()
        time.sleep(PRESS_DELAY)

    def _button(self, button: int) -> None:
        d = self._display
        xtest.fake_input(d, X.ButtonPress, button)
        d.sync()
        time.sleep(PRESS_DELAY)
        xtest.fake_input(d, X.ButtonRelease, button)
        d.sync()
        time.sleep(PRESS_DELAY)

    def _drag(self, start: tuple[int, int], end: tuple[int, int], button: int) -> None:
        d = self._display
        self._move(*start)
        xtest.fake_input(d, X.ButtonPress, button)
        d.sync()
        for i in range(1, DRAG_STEPS + 1):
            x = round(start[0] + (end[0] - start[0]) * i / DRAG_STEPS)
            y = round(start[1] + (end[1] - start[1]) * i / DRAG_STEPS)
            self._move(x, y)
        xtest.fake_input(d, X.ButtonRelease, button)
        d.sync()
        time.sleep(PRESS_DELAY)


def _point(value) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"expected [x, y], got {value!r}")
    x, y = int(value[0]), int(value[1])
    if x < 0 or y < 0:
        raise ValueError(f"coordinates must be >= 0, got {value!r}")
    return x, y
