import sys, time
from Xlib import X, XK, display
from Xlib.ext import xtest
d = display.Display(sys.argv[1])
def key(name):
    kc = d.keysym_to_keycode(XK.string_to_keysym(name))
    xtest.fake_input(d, X.KeyPress, kc); d.sync(); time.sleep(0.05)
    xtest.fake_input(d, X.KeyRelease, kc); d.sync()
key("space"); time.sleep(0.3); key("a")
time.sleep(0.3)
xtest.fake_input(d, X.MotionNotify, x=100, y=100); d.sync(); time.sleep(0.1)
xtest.fake_input(d, X.ButtonPress, 1); d.sync(); time.sleep(0.05)
xtest.fake_input(d, X.ButtonRelease, 1); d.sync()
print("sent")
