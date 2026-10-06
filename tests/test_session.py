import os
import tempfile
import time
from pathlib import Path

import pytest
from conftest import (
    FIXTURE,
    SCENE_READY_SECONDS,
    godot_command,
    needs_gamescope,
    needs_godot,
    wait_for_scene,
)

from eyesol8.capture import CaptureError
from eyesol8.session import MissingDependencyError, Session, SessionRegistry


def test_missing_gamescope_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(MissingDependencyError, match="gamescope"):
        Session.start(["true"])


def test_logs_ring_buffer_filters():
    s = Session.__new__(Session)
    s._init_log_buffer(max_lines=3)
    for line in ["a1", "b2", "a3", "b4"]:
        s._append_log(line)
    lines, cursor = s.logs()
    assert lines == ["b2", "a3", "b4"]
    assert cursor == 4
    assert s.logs(tail=1) == (["b4"], 4)
    assert s.logs(grep="^a") == (["a3"], 4)
    assert s.logs(since=3) == (["b4"], 4)
    assert s.logs(since=4) == ([], 4)


@needs_gamescope
@needs_godot
def test_two_concurrent_godot_sessions(no_leaks):
    first = Session.start(godot_command(), cwd=FIXTURE)
    second = None
    try:
        assert first.display.startswith(":")
        assert first.wayland_display
        assert first.wait_for_log(r"Godot Engine", timeout=20)

        second = Session.start(godot_command(), cwd=FIXTURE)
        assert second.display != first.display
        assert second.wayland_display != first.wayland_display
        assert second.wait_for_log(r"Godot Engine", timeout=20)
        assert os.environ.get("DISPLAY") != first.display
    finally:
        r1 = first.stop()
        r2 = second.stop() if second else None
    assert not first.running
    assert isinstance(r1.last_lines, list)
    assert r2 is None or not second.running


@needs_gamescope
def test_stop_returns_exit_code_of_exited_program(no_leaks):
    s = Session.start(["sh", "-c", "echo hello; echo oops >&2; exit 3"])
    s.wait_for_log(r"oops", timeout=10)
    result = s.stop()
    assert result.exit_code == 3
    assert "hello" in result.last_lines and "oops" in result.last_lines


@needs_gamescope
def test_registry_stops_all_and_reaps_idle(no_leaks):
    reg = SessionRegistry(idle_timeout=0.5)
    try:
        a = reg.start(["sleep", "60"])
        b = reg.start(["sleep", "60"])
        assert reg.get(a.id) is a
        time.sleep(0.7)
        reg.touch(b.id)
        reaped = reg.reap_idle()
        assert reaped == [a.id]
        assert not a.running and b.running
    finally:
        reg.shutdown()
    assert not b.running
    assert reg.list() == []


@needs_gamescope
@needs_godot
def test_fixture_scene_ready_within_documented_time(no_leaks):
    started = time.monotonic()
    s = Session.start(godot_command(), cwd=FIXTURE, width=640, height=360)
    try:
        first = s.wait_for_frame(timeout=SCENE_READY_SECONDS)
        assert first.startswith(b"\x89PNG")
        wait_for_scene(s, timeout=SCENE_READY_SECONDS - (time.monotonic() - started))
    finally:
        s.stop()


@needs_gamescope
def test_wait_for_frame_times_out_without_a_window(no_leaks):
    s = Session.start(["sleep", "60"])
    try:
        with pytest.raises(CaptureError, match="window"):
            s.wait_for_frame(timeout=1)
    finally:
        s.stop()


@needs_gamescope
def test_bad_cwd_raises_and_cleans_up(no_leaks, tmp_path):
    before = set(Path(tempfile.gettempdir()).glob("eyesol8-*"))
    with pytest.raises(FileNotFoundError):
        Session.start(["true"], cwd=tmp_path / "missing")
    assert set(Path(tempfile.gettempdir()).glob("eyesol8-*")) == before
