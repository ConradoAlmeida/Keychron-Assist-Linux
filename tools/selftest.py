#!/usr/bin/env python3
"""Environment diagnostics + full-flow test, aimed at VMs.

    python3 tools/selftest.py                 # diagnostics only
    python3 tools/selftest.py --simulate      # simulate the first slot's key
    python3 tools/selftest.py --simulate F17  # simulate a specific slot

`--simulate` injects the slot's key with `wtype` (wlroots/KDE) or, when the
compositor lacks the virtual-keyboard protocol (GNOME), with `tools/keysim.py`
through uinput (sudo). It exercises the chain compositor -> `kassist trigger` ->
`gio launch` without a physical Keychron keyboard. Close the bound app first,
otherwise `gio launch` just focuses the existing window.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kassist  # noqa: E402

OK, WARN, FAIL = "ok", "--", "!!"


def os_release() -> dict[str, str]:
    data = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k] = v.strip().strip('"')
    except OSError:
        pass
    return data


def run(*argv: str) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""


def browser() -> tuple[str, bool, bool]:
    """Return (description, usable_for_webhid, found).

    Snap/Flatpak builds are confined and cannot open /dev/hidraw, so WebHID
    cannot program the key even though the browser itself works.
    """
    found: list[tuple[str, bool]] = []
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "microsoft-edge"):
        path = shutil.which(name)
        if path:
            confined = path.startswith(("/snap/", "/var/lib/flatpak/"))
            found.append((f"{name} ({path})", not confined))
    for desc, usable in found:
        if usable:
            return desc, True, True
    if found:
        return f"{found[0][0]} [confined: no hidraw]", False, True
    if shutil.which("flatpak"):
        refs = run("flatpak", "list", "--app", "--columns=application")
        for line in refs.splitlines():
            if "chromium" in line.lower():
                return f"flatpak {line.strip()} [confined: no hidraw]", False, True
    return "no Chromium-based browser found", False, False


def procs() -> dict[int, str]:
    out = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        if cmd:
            out[int(entry.name)] = cmd
    return out


def matches(cmd: str, patterns: list[str]) -> bool:
    low = cmd.lower()
    return any(p and len(p) > 3 and p.lower() in low for p in patterns)


def desktop_exec(path: str) -> str:
    """First Exec= argv[0] from a .desktop file (bindings often omit 'exec')."""
    try:
        for line in Path(path).read_text(errors="replace").splitlines():
            if line.startswith("Exec="):
                argv = kassist.clean_exec(line[5:])
                return argv[0] if argv else ""
    except OSError:
        pass
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", nargs="?", const="", metavar="SLOT")
    args = parser.parse_args()

    rows: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str = "") -> None:
        rows.append((status, name, detail))

    info = os_release()
    add(OK, "distro", info.get("PRETTY_NAME", "unknown"))
    add(OK, "kernel", platform.release())
    session = os.environ.get("XDG_SESSION_TYPE", "?")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "?")
    add(OK, "session", f"{session} / {desktop}")
    shell = first_line(run("gnome-shell", "--version"))
    add(OK if "GNOME" in desktop else WARN, "gnome-shell", shell or "not found")
    add(OK, "python", platform.python_version())

    for cmd, optional in (("gsettings", False), ("gio", False), ("xdg-mime", False),
                          ("update-desktop-database", False), ("systemctl", False),
                          ("zenity", True), ("wtype", True), ("notify-send", True)):
        found = shutil.which(cmd)
        if found:
            add(OK, f"cmd {cmd}", found)
        else:
            add(WARN if optional else FAIL, f"cmd {cmd}", "optional" if optional else "required")
    bdesc, busable, bfound = browser()
    if not bfound:
        add(FAIL, "browser", bdesc)
    elif busable:
        add(OK, "browser", bdesc)
    else:
        add(WARN, "browser", bdesc + " — use Chrome/Chromium .deb for WebHID")

    service = run("systemctl", "--user", "is-active", "kassist")
    add(OK if service == "active" else FAIL, "kassist service", service or "stopped")
    enabled = run("systemctl", "--user", "is-enabled", "kassist")
    add(OK if enabled == "enabled" else WARN, "starts at login", enabled or "disabled")

    with socket.socket() as sock:
        sock.settimeout(0.5)
        listening = sock.connect_ex(("127.0.0.1", kassist.DEFAULT_PORT)) == 0
    add(OK if listening else FAIL, f"port {kassist.DEFAULT_PORT}", "listening" if listening else "closed")

    handler = run("xdg-mime", "query", "default", "x-scheme-handler/launcherassist")
    add(OK if handler else FAIL, "launcherassist:// handler", handler or "not registered")

    keyvals = kassist.system_keyvals()
    usable = {s: k for s, k in keyvals.items() if k and not k.startswith(kassist.XKB_OWNED_PREFIXES)}
    add(OK if usable else FAIL, "keys in XKB",
        ", ".join(f"{s}={k}" for s, k in usable.items()) or "none")

    if kassist.installed_backend() == "evdev":
        add(WARN, "GNOME shortcuts", "not used (evdev backend)")
    elif not kassist.gnome_schema_available():
        add(WARN, "GNOME shortcuts", "not available (no GNOME shortcut backend)")
    else:
        paths = subprocess.run(["gsettings", "get", kassist.SCHEMA, "custom-keybindings"],
                               capture_output=True, text=True).stdout
        registered = kassist.parse_paths(paths)
        bad = []
        for slot, keyval in usable.items():
            path = f"{kassist.SHORTCUT_BASE}kassist-{slot.lower()}/"
            binding = run("gsettings", "get", f"{kassist.SCHEMA}.custom-keybinding:{path}", "binding").strip("'")
            if path not in registered or binding != keyval:
                bad.append(slot)
        add(OK if not bad else FAIL, "GNOME shortcuts",
            f"{len(usable) - len(bad)}/{len(usable)}" + (f" (missing: {', '.join(bad)})" if bad else ""))

    hidraw = kassist.keychron_hidraw_nodes()
    writable = [n for n in hidraw if os.access(n, os.W_OK)]
    add(OK if writable else WARN, "hidraw (site WebHID)",
        f"{len(writable)}/{len(hidraw)} writable" if writable else
        ("run: sudo ./install.sh --rules" if hidraw else "keyboard not connected"))

    store = kassist.Store()
    binds = store.all()
    broken = [b["opt_data"].get("path") for b in binds
              if b.get("opt_type") == "App" and b.get("opt_data", {}).get("path")
              and not Path(b["opt_data"]["path"]).exists()]
    add(OK if binds else WARN, "bindings", ", ".join(f"{b['key']} -> {b['opt_data'].get('name')}" for b in binds) or "none")
    add(OK if not broken else FAIL, "app paths exist", "; ".join(broken) or "yes")

    apps = kassist.scan_apps(with_icons=False)
    add(OK if apps else FAIL, "apps found", str(len(apps)))

    if shutil.which("gsettings"):
        show = run("gsettings", "get", "org.gnome.desktop.interface", "clock-format")  # sanity: dconf reachable
        add(OK if show else WARN, "dconf reachable", "yes" if show else "no (graphical session?)")

    width = max(len(name) for _, name, _ in rows) + 2
    print()
    for status, name, detail in rows:
        print(f"[{status}] {name:<{width}} {detail}")
    failures = sum(1 for s, _, _ in rows if s == FAIL)
    print(f"\n{len(rows)} checks, {failures} failure(s)")

    if args.simulate is None:
        return 1 if failures else 0

    slot = args.simulate or (binds[0]["key"] if binds else "")
    if not slot:
        print("\n--simulate: no binding to test (set one up in Quick Start)")
        return 1
    keyval = keyvals.get(slot, "")
    if not keyval:
        print(f"\n--simulate: no keyval for {slot}")
        return 1
    if not shutil.which("wtype"):
        print("\n--simulate: install wtype (apt install wtype) to simulate the key")
        return 1

    target_bind = next((b for b in binds if b["key"] == slot), None)
    patterns: list[str] = []
    if target_bind and target_bind.get("opt_type") == "App":
        data = target_bind.get("opt_data") or {}
        exe = (data.get("exec") or [""])[0] if isinstance(data.get("exec"), list) else ""
        path = data.get("path", "")
        if not exe and path:
            exe = desktop_exec(path)
        patterns = [Path(exe).name, Path(path).stem]

    before = {pid for pid, cmd in procs().items() if matches(cmd, patterns)}
    print(f"\n-> simulating {slot} ({keyval})...")

    injected = False
    if shutil.which("wtype"):
        proc = subprocess.run(["wtype", "-P", keyval, "-s", "50", "-p", keyval],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            injected = True
        elif "virtual keyboard protocol" in (proc.stderr + proc.stdout):
            print("   wtype cannot inject here (compositor lacks zwp_virtual_keyboard_v1);"
                  "\n   falling back to the uinput helper (sudo).")

    if not injected:
        helper = Path(__file__).with_name("keysim.py")
        if helper.exists():
            print(f"   -> sudo python3 {helper} {slot}")
            if subprocess.run(["sudo", sys.executable, str(helper), slot]).returncode == 0:
                injected = True
        if not injected:
            print("   no simulator available: install wtype (wlroots/KDE) or python3-evdev"
                  "\n   and run tools/keysim.py as root")
            return 2

    launched = None
    for _ in range(20):
        time.sleep(0.3)
        new = {pid for pid, cmd in procs().items() if matches(cmd, patterns)} - before
        if new:
            launched = new
            break
    if launched:
        print(f"   OK: new process {sorted(launched)} -> {slot} opened the app")
        return 0
    print("   nothing new appeared. Common causes: the app was already open, the"
          "\n   shortcut is not registered, or GNOME has not reloaded the grab (log out).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
