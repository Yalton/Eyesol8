"""Wayland-native GTK4 test app: red window; a key turns it blue and prints
KEY <name>, a click (any button) turns it green and prints
CLICK (x, y) button <n>.

Run with the system python3 (needs PyGObject and GTK 4).
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

CSS = b"""
window, window.red { background: rgb(255, 0, 0); }
window.blue { background: rgb(0, 0, 255); }
window.green { background: rgb(0, 255, 0); }
"""


def set_color(window, name):
    for c in ("red", "blue", "green"):
        window.remove_css_class(c)
    window.add_css_class(name)


def on_activate(app):
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    window = Gtk.ApplicationWindow(application=app, title="eyesol8-gtk")
    window.set_default_size(640, 360)
    window.set_decorated(False)

    keys = Gtk.EventControllerKey()

    def on_key(_ctl, keyval, _code, _state):
        print("KEY", Gdk.keyval_name(keyval), flush=True)
        set_color(window, "blue")
        return True

    keys.connect("key-pressed", on_key)
    window.add_controller(keys)

    click = Gtk.GestureClick()
    click.set_button(0)  # every button, not just the primary one

    def on_click(gesture, _n, x, y):
        print(f"CLICK ({x:.1f}, {y:.1f}) button {gesture.get_current_button()}", flush=True)
        set_color(window, "green")

    click.connect("pressed", on_click)
    window.add_controller(click)

    window.present()
    print("GTK ready on", Gdk.Display.get_default().get_name(), flush=True)


app = Gtk.Application(application_id="dev.eyesol8.GtkFixture")
app.connect("activate", on_activate)
sys.exit(app.run([]))
