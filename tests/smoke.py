#!/usr/bin/env python3
"""Smoke test for the kassist service (no browser, no keyboard).

    python3 tests/smoke.py              # service already on the default port
    python3 tests/smoke.py --port 40004 # throwaway test instance

Covers: handshake, GetVersion, Get, Add/Get/Remove of a binding, rejection of a
foreign origin and refusal to run a path outside the app list.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kassist import DEFAULT_PORT, ws_frame  # noqa: E402

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
GOOD_ORIGIN = "https://launcher.keychron.com"
TEST_VP = 42


async def handshake(port: int, origin: str | None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    key = base64.b64encode(os.urandom(16)).decode()
    headers = [
        "GET / HTTP/1.1",
        "Host: localhost",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if origin:
        headers.append(f"Origin: {origin}")
    writer.write(("\r\n".join(headers) + "\r\n\r\n").encode())
    await writer.drain()
    status = (await reader.readuntil(b"\r\n\r\n")).split(b"\r\n", 1)[0].decode()
    return status, reader, writer


async def recv(reader):
    b1, b2 = await reader.readexactly(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await reader.readexactly(8))[0]
    payload = await reader.readexactly(n) if n else b""
    return json.loads(payload or b"{}")


async def send(writer, evt_type: str, cmd: str, data=""):
    writer.write(ws_frame(json.dumps({"evt_type": evt_type, "evt_data": {"cmd": cmd, "data": data}}).encode()))
    await writer.drain()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    ok = True

    status, reader, writer = await handshake(args.port, "https://evil.example")
    print("foreign origin ->", status)
    ok &= "403" in status
    writer.close()

    status, reader, writer = await handshake(args.port, GOOD_ORIGIN)
    print("launcher origin ->", status)
    ok &= "101" in status

    await send(writer, "Common", "GetVersion")
    r = await recv(reader)
    version = r["evt_data"]["data"]["version"]
    print("GetVersion ->", version)
    ok &= version == "1.1.6"

    await send(writer, "QuickStart", "Get")
    start = await recv(reader)
    total = start["evt_data"]["data"]["count"]
    apps = []
    while True:
        body = (await recv(reader))["evt_data"]["data"]
        if body["action"] == "Fetch":
            apps.append(body["data"])
        elif body["action"] == "FetchEnd":
            break
    print(f"Get -> {len(apps)}/{total} apps, first: {apps[0]['name']}")
    ok &= total == len(apps) > 0 and "icon_path" in apps[0]

    target = next(a for a in apps if a["path"].endswith(".desktop"))
    bind = {"opt_type": "App", "opt_data": {"name": target["name"], "path": target["path"]}}
    await send(writer, "QuickStart", "AddKeyBind", json.dumps({"vp_id": TEST_VP, "key": "F16", "bind": bind}))
    r = await recv(reader)
    print("AddKeyBind ->", r["evt_data"]["data"])
    ok &= r["evt_data"]["data"] == "Success"

    await send(writer, "QuickStart", "GetKeyBinds", json.dumps(TEST_VP))
    r = await recv(reader)
    print("GetKeyBinds ->", r["evt_data"]["data"])
    ok &= len(r["evt_data"]["data"]) == 1 and r["evt_data"]["data"][0]["key"] == "F16"

    await send(writer, "SysCmd", "SpawnAppByPath", "/usr/bin/true")
    r = await recv(reader)
    print("SpawnAppByPath(/usr/bin/true) ->", r["evt_data"]["data"])
    ok &= r["evt_data"]["data"] is False

    await send(writer, "QuickStart", "RemoveKeyBind", json.dumps({"vp_id": TEST_VP, "key": "F16", "bind": bind}))
    r = await recv(reader)
    print("RemoveKeyBind ->", r["evt_data"]["data"])
    ok &= r["evt_data"]["data"] == "Success"

    writer.close()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
