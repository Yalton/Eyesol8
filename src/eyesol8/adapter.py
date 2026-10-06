"""Client for an in-process engine adapter, e.g. adapters/godot.

The adapter listens on 127.0.0.1:$EYESOL8_ADAPTER_PORT and speaks
newline-delimited JSON. The first line must be {"hello": token}; then each
request {"id", "cmd", "args"} gets a reply {"id", "ok", "result" | "error"}.
See README.md for the commands.
"""

from __future__ import annotations

import base64
import itertools
import json
import secrets
import socket
import threading
import time
from typing import Any

PORT_ENV = "EYESOL8_ADAPTER_PORT"
TOKEN_ENV = "EYESOL8_ADAPTER_TOKEN"


class AdapterError(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def new_token() -> str:
    return secrets.token_hex(16)


class AdapterClient:
    """One connection to an adapter, opened on first use."""

    def __init__(self, port: int, token: str) -> None:
        self.port = port
        self.token = token
        self._sock: socket.socket | None = None
        self._file = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def request(self, cmd: str, args: dict[str, Any] | None = None, timeout: float = 10.0) -> Any:
        """Send one command and return its result; raise AdapterError on failure."""
        with self._lock:
            deadline = time.monotonic() + timeout
            if self._sock is None:
                self._connect(deadline)
            request_id = next(self._ids)
            try:
                self._send({"id": request_id, "cmd": cmd, "args": args or {}})
                while True:
                    reply = self._receive(deadline)
                    if reply.get("id") == request_id:
                        break
            except (OSError, AdapterError):
                self._close_locked()
                raise
        if not reply.get("ok"):
            raise AdapterError(f"adapter {cmd} failed: {reply.get('error')}")
        return reply.get("result")

    def screenshot(self, timeout: float = 10.0) -> bytes:
        result = self.request("screenshot", timeout=timeout)
        return base64.b64decode(result["png"])

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _connect(self, deadline: float) -> None:
        # The engine opens its listener a moment after the program starts.
        while True:
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=1)
                break
            except OSError as e:
                if time.monotonic() > deadline:
                    raise AdapterError(
                        f"no engine adapter on port {self.port} ({e}); is the "
                        "Eyesol8 adapter installed and enabled in the program?"
                    ) from None
                time.sleep(0.1)
        self._sock = sock
        self._file = sock.makefile("rb")
        self._send({"hello": self.token})
        reply = self._receive(deadline)
        if not reply.get("ok"):
            self._close_locked()
            raise AdapterError(f"adapter rejected the connection: {reply.get('error')}")

    def _send(self, message: dict[str, Any]) -> None:
        assert self._sock is not None
        self._sock.sendall(json.dumps(message).encode() + b"\n")

    def _receive(self, deadline: float) -> dict[str, Any]:
        assert self._sock is not None and self._file is not None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AdapterError("adapter did not reply in time")
        self._sock.settimeout(remaining)
        try:
            line = self._file.readline()
        except TimeoutError:
            raise AdapterError("adapter did not reply in time") from None
        if not line:
            raise AdapterError("adapter closed the connection")
        return json.loads(line)

    def _close_locked(self) -> None:
        if self._file is not None:
            self._file.close()
        if self._sock is not None:
            self._sock.close()
        self._sock = None
        self._file = None
