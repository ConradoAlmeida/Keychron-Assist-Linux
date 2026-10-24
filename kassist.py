#!/usr/bin/env python3
"""Keychron Assist for Linux — unofficial local helper for launcher.keychron.com.

The Keychron Launcher web app talks to a local helper over a WebSocket:

    ws://localhost:40003
    {"evt_type": "QuickStart" | "Common" | "SysCmd",
     "evt_data": {"cmd": "...", "data": ...}}

Quick-start "slots" are plain QMK keycodes written into the keyboard by the web
UI (F16..F20). This daemon serves the app list, stores slot -> app bindings and
opens the app when a slot fires — either through desktop shortcuts registered in
the compositor (`kassist setup-shortcuts`, no privileges needed) or by reading
evdev directly (`kassist run --watch`, needs a udev rule).

Commands: run (default), trigger, doctor, setup-shortcuts, remove-shortcuts,
list-apps, sniff, protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import configparser
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

APP_ID = "keychron-assist-linux"
VERSION = "0.1.0"
# version the website expects from Keychron Assist (1.1.2+ enables "open app")
ASSIST_VERSION = "1.1.6"
DEFAULT_PORT = 40003
PORT = DEFAULT_PORT  # backwards compatibility
KEYCHRON_VID = "3434"

# only these origins may talk to the service (keeps arbitrary websites out)
ALLOWED_ORIGINS = {
    "https://launcher.keychron.com",
    "https://www.launcher.keychron.com",
    "https://launcher.keychron.cn",
}

CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID
BINDS_FILE = CONF_DIR / "binds.json"

# evdev codes for the quick-start slots handled by the evdev backend
SLOT_KEYS = {186: "F16", 187: "F17", 188: "F18", 189: "F19", 190: "F20"}

XKB_INET = Path(os.environ.get("KASSIST_XKB_INET", "/usr/share/X11/xkb/symbols/inet"))
XKB_OWNED_PREFIXES = (
    "XF86Audio", "XF86MonBrightness", "XF86Display", "XF86Touchpad",
    "XF86Sleep", "XF86WLAN", "XF86Bluetooth", "XF86Kbd",
)

SLOTS = ["F16", "F17", "F18", "F19", "F20"]


def log(*args: object) -> None:
    print("[kassist]", *args, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Application discovery
# --------------------------------------------------------------------------

def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def desktop_dirs() -> list[Path]:
    dirs = [data_home() / "applications"]
    for base in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if base:
            dirs.append(Path(base) / "applications")
    dirs += [
        Path("/var/lib/flatpak/exports/share/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/snapd/desktop/applications"),
    ]
    seen, out = set(), []
    for d in dirs:
        if str(d) not in seen:
            seen.add(str(d))
            out.append(d)
    return out


def icon_dirs() -> list[Path]:
    dirs = [data_home() / "icons", Path("/usr/share/icons"), Path("/usr/share/pixmaps"),
            Path("/var/lib/flatpak/exports/share/icons"),
            Path.home() / ".local/share/flatpak/exports/share/icons"]
    return [d for d in dirs if d.is_dir()]


ICON_DIRS = icon_dirs()
ICON_SIZES = ("48x48", "64x64", "32x32", "128x128", "256x256", "scalable")
_icon_cache: dict[str, str] = {}


def resolve_icon(name: str) -> str | None:
    if not name:
        return None
    if name in _icon_cache:
        return _icon_cache[name] or None
    candidates: list[Path] = []
    if os.path.isabs(name) and Path(name).is_file():
        candidates.append(Path(name))
    else:
        for root in ICON_DIRS:
            for size in ICON_SIZES:
                for ext in (".png", ".svg", ".xpm"):
                    candidates.append(root / "hicolor" / size / "apps" / f"{name}{ext}")
            for ext in (".png", ".svg", ".xpm"):
                candidates.append(root / f"{name}{ext}")
    found = next((c for c in candidates if c.is_file()), None)
    _icon_cache[name] = str(found) if found else ""
    return _icon_cache[name] or None


def clean_exec(exec_line: str) -> list[str]:
    try:
        parts = shlex.split(exec_line)
    except ValueError:
        parts = exec_line.split()
    drop = {"%f", "%F", "%u", "%U", "%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"}
    out = []
    for part in parts:
        if part in drop or (part.startswith("%") and len(part) == 2):
            continue
        out.append(part.replace("%%", "%"))
    return out


def icon_payload(path: str | None, with_icons: bool) -> dict[str, str]:
    payload = {"icon": "", "icon_path": path or ""}
    if not with_icons or not path:
        return payload
    try:
        if os.path.getsize(path) > 48 * 1024:
            return payload
        data = Path(path).read_bytes()
    except OSError:
        return payload
    mime = "image/svg+xml" if path.endswith(".svg") else "image/png"
    payload["icon"] = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return payload


def scan_apps(with_icons: bool = True) -> list[dict]:
    apps: list[dict] = []
    seen: set[str] = set()
    for directory in desktop_dirs():
        if not directory.is_dir():
            continue
        for entry in sorted(directory.glob("*.desktop")):
            if str(entry) in seen:
                continue
            seen.add(str(entry))
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(entry, encoding="utf-8")
                section = parser["Desktop Entry"]
            except Exception:
                continue
            if section.get("Type", "Application") != "Application":
                continue
            if section.get("NoDisplay", "false").lower() == "true":
                continue
            if section.get("Hidden", "false").lower() == "true":
                continue
            if not section.get("Exec"):
                continue
            apps.append({
                "name": (section.get("Name") or entry.stem).strip(),
                "path": str(entry),
                "exec": clean_exec(section.get("Exec", "")),
                "terminal": section.get("Terminal", "false").lower() == "true",
                **icon_payload(resolve_icon(section.get("Icon", "")), with_icons),
            })
    apps.sort(key=lambda a: a["name"].lower())
    return apps


# --------------------------------------------------------------------------
# Launching
# --------------------------------------------------------------------------

def launch(target: str, exec_argv: list[str] | None = None, terminal: bool = False) -> bool:
    if not target:
        return False
    if target.startswith(("http://", "https://")):
        argv = ["xdg-open", target]
    elif target.endswith(".desktop") and Path(target).is_file():
        argv = ["gio", "launch", target]
    elif Path(target).is_file() and exec_argv:
        argv = exec_argv
    elif Path(target).is_file():
        argv = [target]
    else:
        argv = ["xdg-open", target]
    if terminal and shutil.which("x-terminal-emulator"):
        argv = ["x-terminal-emulator", "-e"] + argv
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, cwd=str(Path.home()))
        log("launched:", " ".join(argv))
        return True
    except Exception as exc:  # noqa: BLE001
        log("launch failed:", argv, exc)
        return False


def launch_bind(bind: dict) -> bool:
    data = bind.get("opt_data") or {}
    if bind.get("opt_type") == "Url":
        return launch(data.get("path") or data.get("url") or "")
    return launch(data.get("path") or "", data.get("exec"), bool(data.get("terminal")))


# --------------------------------------------------------------------------
# Bind storage
# --------------------------------------------------------------------------

class Store:
    def __init__(self, path: Path = BINDS_FILE) -> None:
        self.path = path
        self.data: dict[str, dict[str, dict]] = {}
        self.load()

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text())
        except Exception:
            self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)

    def put(self, vp_id: int, key: str, bind: dict) -> None:
        self.data.setdefault(str(vp_id), {})[key] = bind
        self.save()

    def remove(self, vp_id: int, key: str) -> None:
        binds = self.data.get(str(vp_id), {})
        binds.pop(key, None)
        if not binds:
            self.data.pop(str(vp_id), None)
        self.save()

    def clear(self, vp_id: int) -> None:
        self.data.pop(str(vp_id), None)
        self.save()

    def all(self) -> list[dict]:
        return [{"key": key, **bind} for binds in self.data.values() for key, bind in binds.items()]

    def for_key(self, key: str) -> list[dict]:
        return [b for binds in self.data.values() for k, b in binds.items() if k == key]

    def keys_for(self, vp_id: int) -> dict[str, dict]:
        return self.data.get(str(vp_id), {})


# --------------------------------------------------------------------------
# WebSocket server (stdlib only)
# --------------------------------------------------------------------------

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < 1 << 16:
        header += bytes([126]) + struct.pack(">H", n)
    else:
        header += bytes([127]) + struct.pack(">Q", n)
    return header + payload


class Client:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._lock = asyncio.Lock()

    async def send(self, obj: dict) -> None:
        async with self._lock:
            self.writer.write(ws_frame(json.dumps(obj).encode()))
            await self.writer.drain()

    async def read_frame(self) -> tuple[int, bytes]:
        b1, b2 = await self.reader.readexactly(2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", await self.reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await self.reader.readexactly(8))[0]
        if length > 1 << 20:  # 1 MiB: nothing legitimate comes close
            raise ValueError("frame too large")
        mask = await self.reader.readexactly(4) if masked else b""
        payload = await self.reader.readexactly(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload


class Server:
    def __init__(self, store: Store, apps: list[dict], with_icons: bool, port: int = DEFAULT_PORT) -> None:
        self.store = store
        self.apps = apps
        self.with_icons = with_icons
        self.port = port
        self.allowed_paths = {str(app["path"]) for app in apps}
        self.clients: set[Client] = set()

    @staticmethod
    def env(cmd: str, data, evt_type: str = "QuickStart") -> dict:
        return {"evt_type": evt_type, "evt_data": {"cmd": cmd, "data": data}}

    @staticmethod
    def _vp_id(raw) -> int | None:
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def path_allowed(self, path: str) -> bool:
        """Only launch apps the site itself listed (or a .desktop from the menu dirs)."""
        if not path or path.startswith("-"):
            return False
        if path in self.allowed_paths:
            return True
        p = Path(path)
        if p.suffix != ".desktop" or not p.is_file():
            return False
        return any(p.parent == d for d in desktop_dirs())

    async def handler(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except Exception:
            writer.close()
            return
        lines = request.decode("latin-1").split("\r\n")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        origin = headers.get("origin")
        if origin and origin.rstrip("/") not in ALLOWED_ORIGINS:
            log("rejected origin:", origin)
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        key = headers.get("sec-websocket-key", "")
        if not key:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await writer.drain()

        client = Client(reader, writer)
        self.clients.add(client)
        log("launcher connected")
        try:
            while True:
                opcode, payload = await client.read_frame()
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    writer.write(ws_frame(payload, 0xA))
                    await writer.drain()
                    continue
                if opcode not in (0x1, 0x2):
                    continue
                try:
                    msg = json.loads(payload.decode() or "{}")
                except Exception:
                    continue
                try:
                    await self.dispatch(client, msg)
                except Exception as exc:  # noqa: BLE001
                    log("command failed:", exc)
        except (asyncio.IncompleteReadError, ConnectionResetError, ValueError):
            pass
        finally:
            self.clients.discard(client)
            log("launcher disconnected")
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def dispatch(self, client: Client, msg: dict) -> None:
        data = msg.get("evt_data") or {}
        cmd = data.get("cmd") or ""
        raw = data.get("data")

        if cmd == "GetVersion":
            await client.send(self.env("GetVersion", {"version": ASSIST_VERSION}, "Common"))

        elif cmd == "Get":
            total = len(self.apps)
            await client.send(self.env("Get", {"action": "FetchStart", "count": total}))
            for app in self.apps:
                await client.send(self.env("Get", {"action": "Fetch", "count": total, "data": app}))
            await client.send(self.env("Get", {"action": "FetchEnd", "count": total}))

        elif cmd in ("AddKeyBind", "UpdateKeyBind"):
            body = json.loads(raw or "{}")
            bind = body.get("bind") or {}
            target = (bind.get("opt_data") or {}).get("path") or ""
            ok = (bind.get("opt_type") == "Url" and str(target).startswith(("http://", "https://"))) \
                or (bind.get("opt_type") == "App" and self.path_allowed(str(target)))
            if not ok:
                log("rejected binding:", target)
                await client.send(self.env(cmd, "Invalid"))
                return
            self.store.put(int(body.get("vp_id") or 0), str(body.get("key")), bind)
            await client.send(self.env(cmd, "Success"))

        elif cmd == "RemoveKeyBind":
            body = json.loads(raw or "{}")
            self.store.remove(int(body.get("vp_id") or 0), str(body.get("key")))
            await client.send(self.env(cmd, "Success"))

        elif cmd == "GetKeyBinds":
            vp_id = self._vp_id(raw)
            binds = ([{"key": k, **b} for k, b in self.store.keys_for(vp_id).items()]
                     if vp_id is not None else self.store.all())
            await client.send(self.env(cmd, binds))

        elif cmd == "ClearKeyBind":
            self.store.clear(self._vp_id(raw) or 0)
            await client.send(self.env(cmd, "Success"))

        elif cmd == "SpawnAppByPath":
            target = str(raw or "")
            if not self.path_allowed(target):
                log("refused to spawn:", target)
                await client.send(self.env(cmd, False, "SysCmd"))
                return
            await client.send(self.env(cmd, launch(target), "SysCmd"))

        elif cmd == "SelectAppByNative":
            picked = await self.pick_file()
            for app in picked:
                self.allowed_paths.add(app["path"])
            await client.send(self.env("AddApp", picked))

        elif cmd in ("AddApp", "RemoveApp", "SetReceiverVpidMap", "ClearReceiverVpidMap"):
            await client.send(self.env(cmd, "Success"))

        else:
            log("unknown command:", cmd)
            await client.send(self.env(cmd, "Success"))

    async def pick_file(self) -> list[dict]:
        if not shutil.which("zenity"):
            return []
        proc = await asyncio.create_subprocess_exec(
            "zenity", "--file-selection", "--title=Select an application",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        path = out.decode().strip()
        if not path:
            return []
        name, icon = Path(path).stem, None
        if path.endswith(".desktop"):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                section = parser["Desktop Entry"]
                name = section.get("Name", name)
                icon = resolve_icon(section.get("Icon", ""))
            except Exception:
                pass
        return [{"name": name, "path": path, **icon_payload(icon, self.with_icons)}]

    async def run(self) -> None:
        server = await asyncio.start_server(self.handler, "127.0.0.1", self.port)
        log(f"listening on ws://127.0.0.1:{self.port} ({len(self.apps)} apps)")
        async with server:
            await server.serve_forever()


# --------------------------------------------------------------------------
# evdev backend (optional)
# --------------------------------------------------------------------------

EVENT = struct.Struct("llHHi")
EV_KEY = 0x01


def keychron_event_nodes() -> list[str]:
    nodes = []
    for entry in sorted(Path("/sys/class/input").glob("event*")):
        try:
            text = (entry / "device/uevent").read_text()
        except OSError:
            continue
        product = next((ln.split("=", 1)[1] for ln in text.splitlines()
                        if ln.startswith("PRODUCT=")), "")
        parts = product.split("/")
        if len(parts) >= 3 and parts[1].upper() == KEYCHRON_VID:
            nodes.append(f"/dev/input/{entry.name}")
    return nodes


def keychron_hidraw_nodes() -> list[str]:
    """/dev/hidraw nodes of the keyboard — the site needs them to write the key."""
    nodes = []
    for entry in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            text = (entry / "device/uevent").read_text()
        except OSError:
            continue
        hid_id = next((ln.split("=", 1)[1] for ln in text.splitlines()
                       if ln.startswith("HID_ID=")), "")
        parts = hid_id.split(":")
        if len(parts) >= 3 and parts[1].lstrip("0").upper() == KEYCHRON_VID.upper():
            nodes.append(f"/dev/{entry.name}")
    return nodes


async def watch_keyboard(store: Store, sniff: bool) -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
    watched: dict[str, int] = {}
    warned: set[str] = set()

    def handle(fd: int, node: str) -> None:
        try:
            buf = os.read(fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            buf = b""
        if not buf:
            loop.remove_reader(fd)
            watched.pop(node, None)
            try:
                os.close(fd)
            except OSError:
                pass
            return
        for off in range(0, len(buf) - EVENT.size + 1, EVENT.size):
            _, _, etype, code, value = EVENT.unpack_from(buf, off)
            if etype == EV_KEY and value == 1:
                loop.call_soon_threadsafe(queue.put_nowait, (node, code))

    def rescan() -> None:
        for node in keychron_event_nodes():
            if node in watched:
                continue
            try:
                fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                if node not in warned:
                    warned.add(node)
                    log(f"{node}: {exc.strerror} (install the udev rule or use shortcuts)")
                continue
            watched[node] = fd
            loop.add_reader(fd, lambda fd=fd, node=node: handle(fd, node))
            log("watching", node)

    async def rescanner() -> None:
        while True:
            rescan()
            await asyncio.sleep(3)

    rescan()
    asyncio.create_task(rescanner())
    while True:
        node, code = await queue.get()
        if sniff:
            log(f"KEY event from {node}: code={code} (0x{code:x})")
            continue
        slot = SLOT_KEYS.get(code)
        if not slot:
            continue
        for bind in store.for_key(slot):
            launch_bind(bind)


# --------------------------------------------------------------------------
# Desktop shortcuts (no privileges needed)
# --------------------------------------------------------------------------

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
SHORTCUT_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"


def system_keyvals() -> dict[str, str]:
    """slot -> keyval delivered by the compositor for FK16..FK20."""
    try:
        text = XKB_INET.read_text(errors="replace")
    except OSError:
        return {slot: slot for slot in SLOTS}
    start = text.find('xkb_symbols "evdev"')
    if start == -1:
        return {slot: slot for slot in SLOTS}
    end = text.find("xkb_symbols", start + 10)
    block = text[start:end if end != -1 else len(text)]
    found: dict[str, str] = {}
    for slot in SLOTS:
        match = re.search(rf"key\s*<FK{slot[1:]}>\s*\{{\s*\[\s*([A-Za-z0-9_]+)", block)
        found[slot] = match.group(1) if match else ""
    return found


def gnome_schema_available() -> bool:
    """True when the GNOME media-keys schema exists (GNOME shortcut backend)."""
    try:
        out = subprocess.run(["gsettings", "list-schemas"], capture_output=True, text=True)
    except OSError:
        return False
    return out.returncode == 0 and SCHEMA in out.stdout.split()


def installed_backend() -> str:
    """Backend of the installed user unit: "evdev" when it runs with --watch."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    try:
        unit = (config / "systemd" / "user" / "kassist.service").read_text()
    except OSError:
        return "gnome"
    for line in unit.splitlines():
        if line.startswith("ExecStart=") and "--watch" in line:
            return "evdev"
    return "gnome"


def gsettings_get(key: str) -> str:
    out = subprocess.run(["gsettings", "get", SCHEMA, key], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def gsettings_set(path: str, key: str, value: str) -> None:
    subprocess.run(["gsettings", "set", f"{SCHEMA}.custom-keybinding:{path}", key, value], check=True)


def parse_paths(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("@as"):
        raw = raw[3:].strip()
    try:
        return [str(p) for p in __import__("ast").literal_eval(raw)]
    except (ValueError, SyntaxError):
        return []


def write_paths(paths: list[str]) -> None:
    subprocess.run(["gsettings", "set", SCHEMA, "custom-keybindings",
                    "[" + ", ".join(f"'{p}'" for p in paths) + "]"], check=True)


def reset_shortcut(path: str) -> None:
    for key in ("name", "command", "binding"):
        subprocess.run(["gsettings", "reset", f"{SCHEMA}.custom-keybinding:{path}", key],
                       capture_output=True)


def setup_shortcuts(binary: str, verbose: bool = True) -> int:
    paths = parse_paths(gsettings_get("custom-keybindings"))
    keyvals = system_keyvals()
    changed = False
    registered = 0
    for slot in SLOTS:
        keyval = keyvals[slot]
        path = f"{SHORTCUT_BASE}kassist-{slot.lower()}/"
        if not keyval or keyval.startswith(XKB_OWNED_PREFIXES):
            if verbose:
                why = "no keyval in XKB" if not keyval else f"{keyval} is already used by the system"
                print(f"  {slot}: {why}, skipping")
            if path in paths:
                paths.remove(path)
                changed = True
            reset_shortcut(path)
            continue
        if path not in paths:
            paths.append(path)
            changed = True
        gsettings_set(path, "name", f"Keychron {slot}")
        gsettings_set(path, "command", f"{binary} trigger {slot}")
        gsettings_set(path, "binding", keyval)
        if verbose:
            print(f"  {slot} (key {keyval}) -> {binary} trigger {slot}")
        registered += 1
    if changed:
        write_paths(paths)
    if verbose:
        print(f"{registered} shortcuts registered")
    return registered


def remove_shortcuts(verbose: bool = True) -> int:
    paths = parse_paths(gsettings_get("custom-keybindings"))
    keep = [p for p in paths if "/kassist-" not in p]
    removed = [p for p in paths if "/kassist-" in p]
    write_paths(keep)
    for path in removed:
        reset_shortcut(path)
    if verbose:
        print(f"{len(removed)} shortcuts removed")
    return len(removed)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    if not args.watch and not args.sniff and not gnome_schema_available():
        log("no GNOME shortcut backend; using the evdev backend")
        args.watch = True
    store = Store()
    server = Server(store, scan_apps(with_icons=not args.no_icons), not args.no_icons, args.port)

    async def runner() -> None:
        if args.watch or args.sniff:
            await asyncio.gather(server.run(), watch_keyboard(store, args.sniff))
        else:
            await server.run()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_trigger(args: argparse.Namespace) -> int:
    binds = Store().for_key(args.slot)
    if not binds:
        log(f"no binding for {args.slot}")
        return 1
    ok = False
    for bind in binds:
        ok |= launch_bind(bind)
    return 0 if ok else 1


def cmd_list_apps(args: argparse.Namespace) -> int:
    for app in scan_apps(with_icons=False):
        print(f"{app['name']}\t{app['path']}")
    return 0


def cmd_protocol(args: argparse.Namespace) -> int:
    if is_running():
        return 0
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "run"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return 0


def cmd_sniff(args: argparse.Namespace) -> int:
    args.watch = True
    args.sniff = True
    return cmd_run(args)


def is_running(port: int = DEFAULT_PORT) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_running(port: int = DEFAULT_PORT, timeout: float = 3.0) -> bool:
    """The service takes ~1s to scan apps before opening the port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running(port):
            return True
        time.sleep(0.2)
    return is_running(port)


def cmd_doctor(args: argparse.Namespace) -> int:
    results: list[tuple[bool, str, str, bool]] = []

    def check(ok: bool, name: str, detail: str = "", optional: bool = False) -> None:
        results.append((ok, name, detail, optional))

    check(sys.version_info >= (3, 9), "python >= 3.9", sys.version.split()[0])
    listening = wait_running()
    active = subprocess.run(["systemctl", "--user", "is-active", "kassist"],
                            capture_output=True, text=True).stdout.strip() == "active"
    check(listening, "service listening on 127.0.0.1:%d" % DEFAULT_PORT,
          "" if listening else ("no response in 3s (check the journal)"
                                if active else "stopped: systemctl --user start kassist"))

    handler = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/launcherassist"],
                             capture_output=True, text=True).stdout.strip()
    check(bool(handler), "launcherassist:// handler", handler or "run ./install.sh")

    keyvals = system_keyvals()
    active = {slot: kv for slot, kv in keyvals.items() if kv and not kv.startswith(XKB_OWNED_PREFIXES)}
    if installed_backend() == "evdev":
        check(True, "compositor shortcuts", "not used (evdev backend)", optional=True)
    elif not gnome_schema_available():
        check(True, "compositor shortcuts", "not used (no GNOME shortcut backend)", optional=True)
    else:
        paths = parse_paths(subprocess.run(["gsettings", "get", SCHEMA, "custom-keybindings"],
                                           capture_output=True, text=True).stdout)
        missing = []
        for slot, keyval in active.items():
            path = f"{SHORTCUT_BASE}kassist-{slot.lower()}/"
            binding = subprocess.run(["gsettings", "get", f"{SCHEMA}.custom-keybinding:{path}", "binding"],
                                     capture_output=True, text=True).stdout.strip().strip("'")
            if path not in paths or binding != keyval:
                missing.append(slot)
        check(not missing, "compositor shortcuts", "missing: " + ", ".join(missing) if missing else
              f"{len(active)} slots: " + ", ".join(f"{s} ({k})" for s, k in active.items()))

    store = Store()
    saved = store.all()
    broken = []
    for bind in saved:
        data = bind.get("opt_data") or {}
        target = data.get("path") or ""
        if bind.get("opt_type") == "App" and target and not Path(target).exists():
            broken.append(target)
    check(not broken, "app paths in bindings exist", "; ".join(broken))
    check(bool(saved), "at least one binding saved",
          "" if saved else "set one up in Quick Start", optional=True)

    devices = keychron_event_nodes()
    readable = [n for n in devices if os.access(n, os.R_OK)]
    check(bool(readable), "evdev backend (optional)",
          f"{len(readable)}/{len(devices)} devices readable" if devices else "no Keychron keyboard",
          optional=True)

    hidraw = keychron_hidraw_nodes()
    writable = [n for n in hidraw if os.access(n, os.W_OK)]
    check(bool(writable), "hidraw access for the site's WebHID",
          f"{len(writable)}/{len(hidraw)} nodes writable" if writable else
          ("install the rule: sudo ./install.sh --rules" if hidraw else "keyboard not connected"),
          optional=True)

    failed = 0
    for ok, name, detail, optional in results:
        mark = "OK  " if ok else ("WARN" if optional else "FAIL")
        failed += 0 if ok or optional else 1
        print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kassist", description="Keychron Assist for Linux (unofficial)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the service (default)")
    run.add_argument("--port", type=int, default=DEFAULT_PORT)
    run.add_argument("--watch", action="store_true", help="read the keyboard via evdev (needs udev)")
    run.add_argument("--sniff", action="store_true", help="log raw keyboard events")
    run.add_argument("--no-icons", action="store_true")
    run.set_defaults(func=cmd_run)

    trigger = sub.add_parser("trigger", help="open the app bound to a slot (used by shortcuts)")
    trigger.add_argument("slot", choices=SLOTS)
    trigger.set_defaults(func=cmd_trigger)

    doctor = sub.add_parser("doctor", help="diagnose the installation")
    doctor.set_defaults(func=cmd_doctor)

    setup = sub.add_parser("setup-shortcuts", help="register compositor shortcuts")
    setup.add_argument("--binary", default=str(Path.home() / ".local/bin/kassist"))
    setup.set_defaults(func=lambda a: 0 if setup_shortcuts(a.binary) else 1)

    remove = sub.add_parser("remove-shortcuts", help="remove compositor shortcuts")
    remove.set_defaults(func=lambda a: 0 if remove_shortcuts() is not None else 1)

    apps = sub.add_parser("list-apps", help="list discovered apps")
    apps.set_defaults(func=cmd_list_apps)

    sniff = sub.add_parser("sniff", help="show raw keyboard events")
    sniff.add_argument("--port", type=int, default=DEFAULT_PORT)
    sniff.set_defaults(func=cmd_sniff)

    proto = sub.add_parser("protocol", help="launcherassist:// URI handler")
    proto.add_argument("uri", nargs="?")
    proto.set_defaults(func=cmd_protocol)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"run", "trigger", "doctor", "setup-shortcuts", "remove-shortcuts",
                "list-apps", "sniff", "protocol"}
    if argv and argv[0] in ("-h", "--help", "--version"):
        pass
    elif not argv or argv[0] not in commands:
        if argv and argv[0] == "--protocol":  # backwards compatibility with the first release
            argv = ["protocol"] + argv[1:]
        else:
            argv = ["run"] + argv
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
