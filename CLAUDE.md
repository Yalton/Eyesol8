# Eyesol8

Shared conventions (`base`, `agent-tool`), git authority, and the verify command are declared in `.fleet.json` and auto-loaded by the global `SessionStart` hook.

## Build & Test

```bash
uv sync
uv run pytest            # integration tests need gamescope + godot on PATH
```

## Architecture Overview

Python MCP server (package `src/eyesol8/`). Each session = one `gamescope --backend headless --xwayland-count 1` process running one child program, isolated from the host desktop (unset `WAYLAND_DISPLAY`/`WAYLAND_SOCKET`, never forward host `DISPLAY`). Screenshots: `gamescopectl screenshot` with the session's `GAMESCOPE_WAYLAND_DISPLAY` (X11 grabs return black). Input: XTEST via `python-xlib` on the session's Xwayland `DISPLAY`. Integration fixture: `tests/fixtures/godot_scene/` (see README).

## Repo-Specific Rules

- Never fall back to the host display. Missing dependency = clear error naming it.
- Tests that launch gamescope/godot must clean up every process they start (check with `pgrep` in teardown).
