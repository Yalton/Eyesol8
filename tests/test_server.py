import base64
import io
import os
from pathlib import Path

import anyio
from conftest import FIXTURE, needs_gamescope, needs_godot
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from PIL import Image

REPO = Path(__file__).parent.parent
TOOLS = {
    "display_start",
    "display_screenshot",
    "display_input",
    "display_logs",
    "display_wait",
    "display_list",
    "display_stop",
    "display_query",
}


def server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command="uv",
        args=["run", "--directory", str(REPO), "eyesol8"],
        env={k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV",)},
    )


def pixel(result, xy):
    images = [c for c in result.content if c.type == "image"]
    assert len(images) == 1, result
    assert images[0].mime_type == "image/png"
    img = Image.open(io.BytesIO(base64.b64decode(images[0].data))).convert("RGB")
    return img.getpixel(xy)


async def call(client, name, args=None):
    result = await client.call_tool(name, args or {})
    assert not result.is_error, result.content
    return result


@needs_gamescope
@needs_godot
def test_server_drives_godot_scene(no_leaks):
    async def run():
        async with Client(server_params(), read_timeout_seconds=60) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            assert TOOLS <= tools

            started = await call(
                client,
                "display_start",
                {
                    "command": ["godot", "--audio-driver", "Dummy", "--path", str(FIXTURE)],
                    "width": 640,
                    "height": 360,
                    "ready_regex": "Godot Engine",
                    "ready_frame": True,
                },
            )
            info = started.structured_content
            sid = info["session_id"]
            assert info["display"].startswith(":") and info["pid"] > 0
            try:
                listed = (await call(client, "display_list")).structured_content
                assert [s["session_id"] for s in listed["result"]] == [sid]

                # Godot shows its grey boot splash before the scene.
                with anyio.fail_after(40):
                    while pixel(
                        await call(client, "display_screenshot", {"session_id": sid}), (5, 5)
                    ) != (255, 0, 0):
                        await anyio.sleep(0.25)

                applied = await call(
                    client, "display_input", {"session_id": sid, "actions": [{"click": [100, 100]}]}
                )
                assert applied.structured_content["result"] == 1
                shot = await call(client, "display_screenshot", {"session_id": sid})
                assert pixel(shot, (5, 5)) == (0, 255, 0)

                engine = await call(
                    client, "display_screenshot", {"session_id": sid, "source": "engine"}
                )
                assert pixel(engine, (5, 5)) == (0, 255, 0)
                tree = await call(
                    client,
                    "display_query",
                    {"session_id": sid, "cmd": "tree", "args": {"max_depth": 1}},
                )
                paths = [n["path"] for n in tree.structured_content["result"]]
                assert "/root/Main" in paths
                bad = await client.call_tool("display_query", {"session_id": sid, "cmd": "explode"})
                assert bad.is_error and "unknown cmd" in bad.content[0].text

                waited = await call(
                    client, "display_wait", {"session_id": sid, "log_regex": r"CLICK \(100"}
                )
                assert "CLICK" in waited.content[0].text
                logs = (
                    await call(client, "display_logs", {"session_id": sid, "grep": "CLICK"})
                ).structured_content
                assert logs["lines"] and logs["cursor"] > 0
            finally:
                stopped = await call(client, "display_stop", {"session_id": sid})
            assert "last_lines" in stopped.structured_content

            missing = await client.call_tool("display_logs", {"session_id": sid})
            assert missing.is_error

    anyio.run(run)


@needs_gamescope
def test_server_stops_sessions_on_exit(no_leaks):
    async def run():
        async with Client(server_params(), read_timeout_seconds=60) as client:
            await call(client, "display_start", {"command": ["sleep", "60"]})

    anyio.run(run)
