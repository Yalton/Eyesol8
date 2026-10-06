"""A minimal Wayland client: just enough wire protocol for what Eyesol8 needs.

VirtualInput holds one wlr virtual pointer (zwlr_virtual_pointer_v1) and one
virtual keyboard (zwp_virtual_keyboard_v1) for the life of a session. Keeping
them connected matters: when a compositor's last pointer or keyboard goes
away it drops that seat capability, and the app loses focus, so input from a
short-lived tool (like one wlrctl run per click) arrives before the app has
focus again and is ignored.

set_output_size gives the headless output a custom mode through
zwlr_output_manager_v1 (wlr-output-management).
"""

from __future__ import annotations

import os
import socket
import struct
import time
from collections.abc import Callable
from pathlib import Path

BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
_WL_DISPLAY = 1
_KEYMAP_FORMAT_XKB_V1 = 1
# Holds the keyboard capability only; keys themselves are typed with wtype.
_MINIMAL_KEYMAP = b"""xkb_keymap {
xkb_keycodes "(unnamed)" { minimum = 8; maximum = 255; };
xkb_types "(unnamed)" { include "complete" };
xkb_compatibility "(unnamed)" { include "complete" };
xkb_symbols "(unnamed)" { };
};
\0"""

EventHandler = Callable[[int, int, bytes], None]


class WaylandError(RuntimeError):
    pass


def _string(value: str) -> bytes:
    data = value.encode() + b"\0"
    return struct.pack("<I", len(data)) + data + b"\0" * (-len(data) % 4)


def _fixed(value: float) -> int:
    return round(value * 256)


class WaylandConnection:
    """One client connection, with the compositor's globals already listed."""

    def __init__(self, runtime_dir: str | os.PathLike, display: str, timeout: float = 5.0):
        path = Path(display) if display.startswith("/") else Path(runtime_dir) / display
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(str(path))
        self._buffer = b""
        self._next_id = 2
        self._globals: dict[str, tuple[int, int]] = {}

        self._registry = self._new_id()
        self._send(_WL_DISPLAY, 1, struct.pack("<I", self._registry))  # get_registry
        self._roundtrip()

    def close(self) -> None:
        self._sock.close()

    def _new_id(self) -> int:
        new = self._next_id
        self._next_id += 1
        return new

    def _bind(self, interface: str, version: int) -> int:
        if interface not in self._globals:
            raise WaylandError(f"compositor does not offer {interface}")
        name, offered = self._globals[interface]
        new = self._new_id()
        payload = (
            struct.pack("<I", name)
            + _string(interface)
            + struct.pack("<II", min(version, offered), new)
        )
        self._send(self._registry, 0, payload)
        return new

    def _send(self, obj: int, opcode: int, payload: bytes, fds: list[int] | None = None) -> None:
        header = struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode)
        if fds:
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack(f"<{len(fds)}i", *fds))]
            self._sock.sendmsg([header + payload], ancillary)
        else:
            self._sock.sendall(header + payload)

    def _roundtrip(self, on_event: EventHandler | None = None) -> None:
        """Flush requests and pass events to ``on_event`` until the compositor catches up."""
        callback = self._new_id()
        self._send(_WL_DISPLAY, 0, struct.pack("<I", callback))  # sync
        while True:
            obj, opcode, payload = self._read_event()
            if obj == _WL_DISPLAY and opcode == 0:  # error(object, code, message)
                _, code = struct.unpack_from("<II", payload)
                (length,) = struct.unpack_from("<I", payload, 8)
                message = payload[12 : 12 + length - 1].decode(errors="replace")
                raise WaylandError(f"compositor error {code}: {message}")
            if obj == self._registry and opcode == 0:  # global(name, interface, version)
                (name,) = struct.unpack_from("<I", payload)
                (length,) = struct.unpack_from("<I", payload, 4)
                interface = payload[8 : 8 + length - 1].decode()
                (version,) = struct.unpack_from("<I", payload, 8 + length + (-length % 4))
                self._globals[interface] = (name, version)
            elif obj == callback and opcode == 0:  # done
                return
            elif on_event is not None:
                on_event(obj, opcode, payload)

    def _read_event(self) -> tuple[int, int, bytes]:
        while len(self._buffer) < 8:
            self._recv()
        obj, word = struct.unpack_from("<II", self._buffer)
        size = word >> 16
        while len(self._buffer) < size:
            self._recv()
        payload = self._buffer[8:size]
        self._buffer = self._buffer[size:]
        return obj, word & 0xFFFF, payload

    def _recv(self) -> None:
        # Events carrying fds (none expected here) would have them dropped.
        data = self._sock.recv(65536)
        if not data:
            raise WaylandError("compositor closed the connection")
        self._buffer += data


class VirtualInput(WaylandConnection):
    def __init__(self, runtime_dir: str | os.PathLike, display: str, timeout: float = 5.0):
        super().__init__(runtime_dir, display, timeout)
        self._start = time.monotonic()

        seat = self._bind("wl_seat", 1)
        pointer_manager = self._bind("zwlr_virtual_pointer_manager_v1", 1)
        keyboard_manager = self._bind("zwp_virtual_keyboard_manager_v1", 1)

        self._pointer = self._new_id()
        # create_virtual_pointer(seat, id)
        self._send(pointer_manager, 0, struct.pack("<II", seat, self._pointer))
        self._keyboard = self._new_id()
        # create_virtual_keyboard(seat, id)
        self._send(keyboard_manager, 0, struct.pack("<II", seat, self._keyboard))
        fd = os.memfd_create("eyesol8-keymap", os.MFD_CLOEXEC)
        try:
            os.write(fd, _MINIMAL_KEYMAP)
            # keymap(format, fd, size): the fd travels as ancillary data.
            self._send(
                self._keyboard,
                0,
                struct.pack("<II", _KEYMAP_FORMAT_XKB_V1, len(_MINIMAL_KEYMAP)),
                fds=[fd],
            )
        finally:
            os.close(fd)
        self._roundtrip()

    def move(self, x: float, y: float, width: int, height: int) -> None:
        # motion_absolute(time, x, y, x_extent, y_extent) + frame
        self._send(
            self._pointer,
            1,
            struct.pack("<IIIII", self._time(), round(x), round(y), width, height),
        )
        self._frame()

    def button(self, button: int, pressed: bool) -> None:
        self._send(self._pointer, 2, struct.pack("<III", self._time(), button, int(pressed)))
        self._frame()

    def scroll(self, notches: int) -> None:
        # axis_source(wheel), axis_discrete(time, vertical, value, discrete), frame
        self._send(self._pointer, 5, struct.pack("<I", 0))
        self._send(
            self._pointer,
            7,
            struct.pack("<IIiI", self._time(), 0, _fixed(15 * notches), notches & 0xFFFFFFFF),
        )
        self._frame()

    def sync(self) -> None:
        self._roundtrip()

    def close(self) -> None:
        try:
            self._send(self._pointer, 8, b"")  # destroy
            self._send(self._keyboard, 3, b"")  # destroy
        except OSError:
            pass
        super().close()

    def _frame(self) -> None:
        self._send(self._pointer, 4, b"")

    def _time(self) -> int:
        return int((time.monotonic() - self._start) * 1000) & 0xFFFFFFFF


# zwlr_output_manager_v1 version 1 event opcodes, numbered as in the protocol XML.
_MANAGER_EV_HEAD, _MANAGER_EV_DONE = 0, 1
_HEAD_EV_NAME, _HEAD_EV_ENABLED = 0, 4
_CONFIG_EV_SUCCEEDED, _CONFIG_EV_FAILED = 0, 1


def set_output_size(
    runtime_dir: str | os.PathLike, display: str, width: int, height: int, timeout: float = 5.0
) -> None:
    """Give the compositor's one enabled output a custom ``width`` x ``height`` mode."""
    conn = WaylandConnection(runtime_dir, display, timeout)
    try:
        manager = conn._bind("zwlr_output_manager_v1", 1)
        heads: dict[int, dict] = {}
        serials: list[int] = []
        outcome: list[int] = []
        config = 0

        def on_event(obj: int, opcode: int, payload: bytes) -> None:
            if obj == manager and opcode == _MANAGER_EV_HEAD:
                heads[struct.unpack_from("<I", payload)[0]] = {"enabled": False, "name": ""}
            elif obj == manager and opcode == _MANAGER_EV_DONE:
                serials.append(struct.unpack_from("<I", payload)[0])
            elif obj in heads and opcode == _HEAD_EV_ENABLED:
                heads[obj]["enabled"] = bool(struct.unpack_from("<i", payload)[0])
            elif obj in heads and opcode == _HEAD_EV_NAME:
                (length,) = struct.unpack_from("<I", payload)
                heads[obj]["name"] = payload[4 : 4 + length - 1].decode(errors="replace")
            elif config and obj == config:
                outcome.append(opcode)

        deadline = time.monotonic() + timeout
        while not serials:
            if time.monotonic() > deadline:
                raise WaylandError("compositor never listed its outputs")
            conn._roundtrip(on_event)
        enabled = [head for head, info in heads.items() if info["enabled"]]
        if len(enabled) != 1:
            raise WaylandError(f"expected one enabled output, found {list(heads.values())}")

        config = conn._new_id()
        conn._send(manager, 0, struct.pack("<II", config, serials[-1]))  # create_configuration
        config_head = conn._new_id()
        conn._send(config, 0, struct.pack("<II", config_head, enabled[0]))  # enable_head
        conn._send(config_head, 1, struct.pack("<iii", width, height, 0))  # set_custom_mode
        conn._send(config, 2, b"")  # apply
        while not outcome:
            if time.monotonic() > deadline:
                raise WaylandError("compositor did not answer the output configuration")
            conn._roundtrip(on_event)
        if outcome[0] != _CONFIG_EV_SUCCEEDED:
            reason = "rejected" if outcome[0] == _CONFIG_EV_FAILED else "cancelled"
            raise WaylandError(f"compositor {reason} the {width}x{height} output mode")
    finally:
        conn.close()
