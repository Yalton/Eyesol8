# Eyesol8

An MCP server that runs any program inside a private, headless display so an
agent can screenshot it and drive it with keyboard and mouse input, without
the program ever touching the user's desktop.

Design doc: https://claude.ai/code/artifact/6a92e49f-d9e5-46ce-a4a5-87675ff655a6

## How it works

- Each session is one `gamescope --backend headless --xwayland-count 1` process
  with one child program. Host `WAYLAND_DISPLAY` / `WAYLAND_SOCKET` are unset and
  the host `DISPLAY` is never forwarded.
- Screenshots come from `gamescopectl screenshot <path>` with
  `GAMESCOPE_WAYLAND_DISPLAY` set to the session's socket. X11 grabs
  (`ffmpeg x11grab`, `XGetImage`) return black frames because Gamescope
  composites client buffers itself.
- Input is XTEST (python-xlib) on the session's Xwayland `DISPLAY`.

## Requirements

- Linux, `gamescope` (with `gamescopectl`)
- Python 3.12+, `uv`
- Godot 4 on `PATH` as `godot` for the integration tests

## Use from Claude Code

Register the server (stdio), pointing `--directory` at this repo:

```bash
claude mcp add eyesol8 -- uv run --directory /path/to/Eyesol8 eyesol8
```

Tools:

- `display_start(command, cwd, env, width, height, ready_regex, ready_seconds)`
  starts a program in its own headless display and returns `session_id`.
- `display_screenshot(session_id, region, scale, save_to)` returns a PNG.
- `display_input(session_id, actions)` sends keys, text, mouse moves, clicks,
  drags and scrolls, e.g. `[{"key": "ctrl+s"}, {"click": [100, 100]}]`.
- `display_logs(session_id, tail, grep, since)` and
  `display_wait(session_id, log_regex, seconds, timeout)` read the program's output.
- `display_list()` and `display_stop(session_id, grace_seconds)` manage sessions.

## Godot adapter (optional)

`adapters/godot/addons/eyesol8_adapter/` is a Godot 4 add-on that gives agents
exact engine frames, the scene tree and InputMap actions. Copy (or symlink) it
into your project's `addons/` and either enable it under Project Settings >
Plugins or add the autoload yourself:

```ini
[autoload]
Eyesol8Adapter="*res://addons/eyesol8_adapter/eyesol8_adapter.gd"
```

It does nothing unless `EYESOL8_ADAPTER_PORT` is set, so it is safe to ship.
Every Eyesol8 session sets `EYESOL8_ADAPTER_PORT` (a free localhost port) and
`EYESOL8_ADAPTER_TOKEN` (a random secret) for its program.

Agents use it through `display_query(session_id, cmd, args)` and
`display_screenshot(..., source="engine")`.

### Protocol

Newline-delimited JSON over TCP on `127.0.0.1:$EYESOL8_ADAPTER_PORT`, one UTF-8
JSON object per line, one client at a time.

1. The client's first line is `{"hello": "<EYESOL8_ADAPTER_TOKEN>"}`. The
   adapter replies `{"ok": true, "engine": "<version>"}`, or
   `{"ok": false, "error": "bad hello"}` and closes the connection.
2. Each request is `{"id": <int>, "cmd": "<name>", "args": {...}}`. Each gets
   exactly one reply, `{"id": <same>, "ok": true, "result": ...}` or
   `{"id": <same>, "ok": false, "error": "<message>"}`. Commands that wait for
   frames can make replies arrive out of order; match them by `id`.

| `cmd` | `args` | `result` |
|---|---|---|
| `screenshot` | none | `{"png": <base64>, "width", "height"}`: the root viewport after the next drawn frame |
| `tree` | `root` (node path, default `/root`), `max_depth` (default 32), `details` (bool) | list of `{"path", "name", "type"}`, depth first; with `details`, CanvasItems add `visible` and 2D/3D/Control nodes add global `position` |
| `action` | `name` (InputMap action), `frames` (default 1) | `true` once the action has been pressed, held for `frames` frames and released (sent as `InputEventAction`, so `_input` and `Input.is_action_pressed` both see it) |
| `frame_wait` | `n` (default 1) | `Engine.get_process_frames()` after `n` frames |
| `time_scale` | `value` (optional) | the previous `Engine.time_scale`; sets it when `value` is given |

Unknown commands, unknown actions and missing nodes return `ok: false`.

## Sessions

Sessions idle for 30 minutes are stopped, and all sessions stop when the server
exits. Pass `ready_frame: true` to `display_start` to wait until the program
has drawn its first frame; screenshots wait for that frame anyway. A Godot
game shows its scene about 2s after start, and input sent earlier is lost.

## Layout

- `src/eyesol8/` — package
- `tests/fixtures/godot_scene/` — 640x360 Godot scene used by integration tests:
  red background, a moving white circle; any key toggles red/blue and prints
  `KEY <name>`, a mouse click turns it green and prints `CLICK (x, y)`.
- `tests/fixtures/spike_xtest.py` — the original spike's XTEST input script.

## Development

```bash
uv sync
uv run pytest
```

Work is tracked in beads (`bd ready`).
