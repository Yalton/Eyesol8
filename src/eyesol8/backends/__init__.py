"""Display backends: how a session gets a private display and screenshots it.

A backend starts the display server and the session shim (see
eyesol8.session), and captures the display. Input is XTEST on the session's
DISPLAY for every backend.
"""

from __future__ import annotations

from eyesol8.backends.base import Backend, BackendError, MissingDependencyError
from eyesol8.backends.gamescope import GamescopeBackend
from eyesol8.backends.xvfb import XvfbBackend

BACKENDS: dict[str, type[Backend]] = {
    "gamescope": GamescopeBackend,
    "xvfb": XvfbBackend,
}


def get_backend(name: str) -> Backend:
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(f"unknown backend {name!r}; choose one of {', '.join(BACKENDS)}") from None


__all__ = ["BACKENDS", "Backend", "BackendError", "MissingDependencyError", "get_backend"]
