#!/usr/bin/env python3
"""Emit a slot key through /dev/uinput — works where wtype does not (GNOME).

GNOME/Mutter does not expose zwp_virtual_keyboard_v1, so wtype cannot inject
keys there. uinput sits below the compositor, so the event looks like a real
keyboard: XKB translates it and the compositor shortcut fires.

Needs root (or a udev rule for /dev/uinput) and python3-evdev:

    sudo python3 tools/keysim.py F16

The keycode is the raw evdev one; the compositor maps F16 -> XF86Launch7 (or
F19/F20 depending on xkeyboard-config), exactly like the physical key.
"""

from __future__ import annotations

import sys
import time

try:
    from evdev import UInput, ecodes
except ImportError:
    sys.exit("python3-evdev is not installed (apt install python3-evdev)")

KEYS = {
    "F16": ecodes.KEY_F16,
    "F17": ecodes.KEY_F17,
    "F18": ecodes.KEY_F18,
    "F19": ecodes.KEY_F19,
    "F20": ecodes.KEY_F20,
}


def main() -> int:
    slot = (sys.argv[1] if len(sys.argv) > 1 else "F16").upper()
    code = KEYS.get(slot)
    if code is None:
        print(f"unknown key: {slot} (use F16..F20)", file=sys.stderr)
        return 2

    device = UInput({ecodes.EV_KEY: [code]}, name="kassist-keysim")
    time.sleep(0.5)  # give the compositor time to notice the new device
    device.write(ecodes.EV_KEY, code, 1)
    device.syn()
    time.sleep(0.1)
    device.write(ecodes.EV_KEY, code, 0)
    device.syn()
    time.sleep(0.2)
    device.close()
    print(f"sent {slot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
