import shutil
import subprocess

from conftest import FIXTURE, needs_godot

ADDON = FIXTURE.parent.parent.parent / "adapters" / "godot" / "addons" / "eyesol8_adapter"

# A helper editor plugin that turns the adapter on the way the Plugins
# screen does, then reports whether the autoload got registered.
ENABLER = """@tool
extends EditorPlugin
func _enter_tree():
	await get_tree().create_timer(1.0).timeout
	EditorInterface.set_plugin_enabled("eyesol8_adapter", true)
	print("ENABLED ", EditorInterface.is_plugin_enabled("eyesol8_adapter"))
	print("AUTOLOAD ", ProjectSettings.has_setting("autoload/Eyesol8Adapter"))
	get_tree().quit()
"""


@needs_godot
def test_enabling_plugin_in_editor_registers_autoload(tmp_path):
    shutil.copytree(ADDON, tmp_path / "addons" / "eyesol8_adapter")
    enabler = tmp_path / "addons" / "enabler"
    enabler.mkdir()
    (enabler / "plugin.cfg").write_text(
        '[plugin]\nname="enabler"\ndescription=""\nauthor=""\nversion="1"\nscript="enabler.gd"\n'
    )
    (enabler / "enabler.gd").write_text(ENABLER)
    (tmp_path / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="plugintest"\n'
        '[editor_plugins]\nenabled=PackedStringArray("res://addons/enabler/plugin.cfg")\n'
    )
    result = subprocess.run(
        ["godot", "--headless", "--editor", "--path", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "ENABLED true" in result.stdout, result.stdout + result.stderr
    assert "AUTOLOAD true" in result.stdout, result.stdout + result.stderr
    assert "SCRIPT ERROR" not in result.stdout + result.stderr
