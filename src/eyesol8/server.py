"""MCP server exposing the display_* tools over stdio."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image

from eyesol8.adapter import AdapterError
from eyesol8.session import Session, SessionRegistry

# One frame at 30 fps: lets the program draw the result of the latest input.
FRAME_SECONDS = 1 / 30

registry = SessionRegistry()
_last_input: dict[str, float] = {}


@asynccontextmanager
async def _lifespan(_server: MCPServer) -> AsyncIterator[None]:
    registry.start_reaper()
    try:
        yield
    finally:
        registry.shutdown()


mcp = MCPServer(
    "eyesol8",
    instructions=(
        "Run a program in a private headless display, then screenshot it and "
        "drive it with keyboard and mouse input. Start with display_start, and "
        "always display_stop sessions you no longer need."
    ),
    lifespan=_lifespan,
)


def _session(session_id: str) -> Session:
    try:
        session = registry.get(session_id)
    except KeyError:
        raise ToolError(f"no session {session_id!r}; display_list shows live sessions") from None
    session.touch()
    return session


def _wait(session: Session, log_regex: str | None, seconds: float | None, timeout: float) -> str:
    if seconds:
        time.sleep(seconds)
    if log_regex is None:
        return f"waited {seconds or 0}s"
    try:
        return session.wait_for_log(log_regex, timeout=timeout)
    except TimeoutError as e:
        raise ToolError(str(e)) from None


@mcp.tool()
def display_start(
    command: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    width: int = 1280,
    height: int = 720,
    ready_regex: str | None = None,
    ready_seconds: float | None = None,
    ready_frame: bool = False,
    backend: str = "gamescope",
) -> dict[str, Any]:
    """Start a program in its own headless display.

    ``backend`` is "gamescope" (default, needs a GPU) or "xvfb" (software
    rendering, no GPU; Godot needs --rendering-driver opengl3 there).

    Optionally waits before returning: ``ready_frame`` until the program has
    drawn its first frame, then ``ready_seconds``, then until a log line
    matches ``ready_regex`` (30s timeout each). A first frame may still be a
    splash screen; games often need a second or two more before input works.
    """
    try:
        session = registry.start(
            command, cwd=cwd, env=env, width=width, height=height, backend=backend
        )
    except (RuntimeError, ValueError, OSError) as e:
        raise ToolError(str(e)) from None
    try:
        if ready_frame:
            try:
                session.wait_for_frame(timeout=30)
            except RuntimeError as e:
                raise ToolError(str(e)) from None
        if ready_regex is not None or ready_seconds:
            _wait(session, ready_regex, ready_seconds, timeout=30)
    except ToolError as e:
        registry.stop(session.id, grace_seconds=0)
        raise ToolError(f"program never became ready: {e}") from None
    return {"session_id": session.id, "display": session.display, "pid": session.pid}


@mcp.tool(structured_output=False)
def display_screenshot(
    session_id: str,
    region: list[int] | None = None,
    scale: float | None = None,
    save_to: str | None = None,
    source: str = "compositor",
) -> list[Any]:
    """Capture the session's display as a PNG.

    ``region`` is [x, y, w, h] in display pixels; ``scale`` in (0, 1] shrinks
    the image; ``save_to`` also writes the PNG to that path. ``source="engine"``
    takes the game's own viewport via the engine adapter (Godot add-on) instead
    of the composited display.
    """
    session = _session(session_id)
    if region is not None and len(region) != 4:
        raise ToolError(f"region must be [x, y, w, h], got {region}")
    wait = _last_input.get(session_id, 0) + FRAME_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        png = session.screenshot(
            region=tuple(region) if region else None, scale=scale, save_to=save_to, source=source
        )
    except (RuntimeError, ValueError, OSError) as e:
        raise ToolError(str(e)) from None
    content: list[Any] = [Image(data=png, format="png")]
    if save_to is not None:
        content.append(f"saved to {save_to}")
    return content


@mcp.tool()
def display_input(session_id: str, actions: list[dict[str, Any]]) -> int:
    """Send keyboard and mouse input; returns how many actions were applied.

    Actions, applied in order: {"key": "ctrl+s"}, {"text": "hello"},
    {"move": [x, y]}, {"click": [x, y], "button": 1},
    {"drag": [[x1, y1], [x2, y2]], "button": 1}, {"scroll": dy} (positive is
    down), {"wait": ms}. A bad action list sends nothing.
    """
    session = _session(session_id)
    try:
        count = session.input(actions)
    except (ValueError, TypeError, RuntimeError) as e:
        raise ToolError(str(e)) from None
    _last_input[session_id] = time.monotonic()
    return count


@mcp.tool()
def display_logs(
    session_id: str, tail: int = 200, grep: str | None = None, since: int | None = None
) -> dict[str, Any]:
    """Return the program's recent stdout/stderr lines.

    Pass the returned ``cursor`` back as ``since`` to get only newer lines.
    """
    session = _session(session_id)
    lines, cursor = session.logs(tail=tail, grep=grep, since=since)
    return {"lines": lines, "cursor": cursor}


@mcp.tool()
def display_wait(
    session_id: str,
    log_regex: str | None = None,
    seconds: float | None = None,
    timeout: float = 30,
) -> str:
    """Wait ``seconds``, then (if given) until a log line matches ``log_regex``.

    Returns the matched line, or an error after ``timeout`` seconds.
    """
    return _wait(_session(session_id), log_regex, seconds, timeout)


@mcp.tool()
def display_query(
    session_id: str, cmd: str, args: dict[str, Any] | None = None, timeout: float = 10
) -> dict[str, Any]:
    """Send a command to the engine adapter inside the program (Godot add-on).

    Commands: "tree" {root?, max_depth?, details?} lists nodes;
    "action" {name, frames?} presses an InputMap action; "frame_wait" {n?};
    "time_scale" {value?} returns the previous scale. For frames use
    display_screenshot with source="engine". Returns {"result": ...}.
    """
    session = _session(session_id)
    try:
        return {"result": session.query(cmd, args, timeout=timeout)}
    except AdapterError as e:
        raise ToolError(str(e)) from None


@mcp.tool()
def display_list() -> list[dict[str, Any]]:
    """List live sessions with their command, size and uptime."""
    now = time.monotonic()
    return [
        {
            "session_id": s.id,
            "command": s.command,
            "width": s.width,
            "height": s.height,
            "uptime_seconds": round(now - s.started_at, 1),
            "backend": s.backend.name,
            "running": s.running,
        }
        for s in registry.list()
    ]


@mcp.tool()
def display_stop(session_id: str, grace_seconds: float = 3) -> dict[str, Any]:
    """Stop the program and its display; returns its exit code and last log lines."""
    try:
        result = registry.stop(session_id, grace_seconds=grace_seconds)
    except KeyError:
        raise ToolError(f"no session {session_id!r}") from None
    _last_input.pop(session_id, None)
    return {"exit_code": result.exit_code, "last_lines": result.last_lines}


def main() -> None:
    try:
        mcp.run("stdio")
    finally:
        registry.shutdown()
