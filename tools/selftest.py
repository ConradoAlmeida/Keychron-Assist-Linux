#!/usr/bin/env python3
"""Diagnóstico de ambiente + teste do fluxo completo, pensado para VMs.

    python3 tools/selftest.py                 # só diagnostica
    python3 tools/selftest.py --simulate      # simula a tecla do primeiro slot
    python3 tools/selftest.py --simulate F17  # simula um slot específico

O `--simulate` injeta o keyval real do slot com `wtype` (Wayland). Isso testa a
cadeia compositor -> `kassist trigger` -> `gio launch`, sem precisar do teclado
Keychron físico. Rode `--simulate` com o app da binding fechado, senão o
`gio launch` só ativa a janela existente.
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


def browser() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "microsoft-edge"):
        path = shutil.which(name)
        if path:
            return f"{name} ({path})"
    if shutil.which("flatpak"):
        refs = run("flatpak", "list", "--app", "--columns=application")
        for line in refs.splitlines():
            if "chromium" in line.lower():
                return f"flatpak {line.strip()}"
    return "nenhum navegador Chromium encontrado"


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", nargs="?", const="", metavar="SLOT")
    args = parser.parse_args()

    rows: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str = "") -> None:
        rows.append((status, name, detail))

    info = os_release()
    add(OK, "distro", info.get("PRETTY_NAME", "desconhecida"))
    add(OK, "kernel", platform.release())
    session = os.environ.get("XDG_SESSION_TYPE", "?")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "?")
    add(OK, "sessão", f"{session} / {desktop}")
    shell = first_line(run("gnome-shell", "--version"))
    add(OK if "GNOME" in desktop else WARN, "gnome-shell", shell or "não encontrado")
    add(OK, "python", platform.python_version())

    for cmd, optional in (("gsettings", False), ("gio", False), ("xdg-mime", False),
                          ("update-desktop-database", False), ("systemctl", False),
                          ("zenity", True), ("wtype", True), ("notify-send", True)):
        found = shutil.which(cmd)
        if found:
            add(OK, f"cmd {cmd}", found)
        else:
            add(WARN if optional else FAIL, f"cmd {cmd}", "opcional" if optional else "obrigatório")
    add(OK if "nenhum" not in (b := browser()) else FAIL, "navegador", b)

    service = run("systemctl", "--user", "is-active", "kassist")
    add(OK if service == "active" else FAIL, "serviço kassist", service or "parado")
    enabled = run("systemctl", "--user", "is-enabled", "kassist")
    add(OK if enabled == "enabled" else WARN, "inicia no login", enabled or "desabilitado")

    with socket.socket() as sock:
        sock.settimeout(0.5)
        listening = sock.connect_ex(("127.0.0.1", kassist.DEFAULT_PORT)) == 0
    add(OK if listening else FAIL, f"porta {kassist.DEFAULT_PORT}", "escutando" if listening else "fechada")

    handler = run("xdg-mime", "query", "default", "x-scheme-handler/launcherassist")
    add(OK if handler else FAIL, "handler launcherassist://", handler or "não registrado")

    keyvals = kassist.system_keyvals()
    usable = {s: k for s, k in keyvals.items() if k and not k.startswith(kassist.XKB_OWNED_PREFIXES)}
    add(OK if usable else FAIL, "teclas no XKB",
        ", ".join(f"{s}={k}" for s, k in usable.items()) or "nenhuma")

    paths = subprocess.run(["gsettings", "get", kassist.SCHEMA, "custom-keybindings"],
                           capture_output=True, text=True).stdout
    registered = kassist.parse_paths(paths)
    bad = []
    for slot, keyval in usable.items():
        path = f"{kassist.SHORTCUT_BASE}kassist-{slot.lower()}/"
        binding = run("gsettings", "get", f"{kassist.SCHEMA}.custom-keybinding:{path}", "binding").strip("'")
        if path not in registered or binding != keyval:
            bad.append(slot)
    add(OK if not bad else FAIL, "atalhos do GNOME",
        f"{len(usable) - len(bad)}/{len(usable)}" + (f" (faltando: {', '.join(bad)})" if bad else ""))

    hidraw = kassist.keychron_hidraw_nodes()
    writable = [n for n in hidraw if os.access(n, os.W_OK)]
    add(OK if writable else WARN, "hidraw (WebHID do site)",
        f"{len(writable)}/{len(hidraw)} graváveis" if writable else
        ("rode: sudo ./install.sh --rules" if hidraw else "teclado não conectado"))

    store = kassist.Store()
    binds = store.all()
    broken = [b["opt_data"].get("path") for b in binds
              if b.get("opt_type") == "App" and b.get("opt_data", {}).get("path")
              and not Path(b["opt_data"]["path"]).exists()]
    add(OK if binds else WARN, "bindings", ", ".join(f"{b['key']} -> {b['opt_data'].get('name')}" for b in binds) or "nenhuma")
    add(OK if not broken else FAIL, "apps existem", "; ".join(broken) or "sim")

    apps = kassist.scan_apps(with_icons=False)
    add(OK if apps else FAIL, "apps encontrados", str(len(apps)))

    if shutil.which("gsettings"):
        show = run("gsettings", "get", "org.gnome.desktop.interface", "clock-format")  # sanity: dconf acessível
        add(OK if show else WARN, "dconf acessível", "sim" if show else "não (sessão gráfica?)")

    width = max(len(name) for _, name, _ in rows) + 2
    print()
    for status, name, detail in rows:
        print(f"[{status}] {name:<{width}} {detail}")
    failures = sum(1 for s, _, _ in rows if s == FAIL)
    print(f"\n{len(rows)} verificações, {failures} falha(s)")

    if args.simulate is None:
        return 1 if failures else 0

    slot = args.simulate or (binds[0]["key"] if binds else "")
    if not slot:
        print("\n--simulate: nenhuma binding para testar (configure uma no Quick Start)")
        return 1
    keyval = keyvals.get(slot, "")
    if not keyval:
        print(f"\n--simulate: sem keyval para {slot}")
        return 1
    if not shutil.which("wtype"):
        print("\n--simulate: instale wtype (apt install wtype) para simular a tecla")
        return 1

    target_bind = next((b for b in binds if b["key"] == slot), None)
    patterns: list[str] = []
    if target_bind and target_bind.get("opt_type") == "App":
        data = target_bind.get("opt_data") or {}
        exe = (data.get("exec") or [""])[0] if isinstance(data.get("exec"), list) else ""
        patterns = [Path(exe).name, Path(data.get("path", "")).stem]

    before = {pid for pid, cmd in procs().items() if matches(cmd, patterns)}
    print(f"\n-> simulando {slot} ({keyval}) com wtype...")
    subprocess.run(["wtype", "-P", keyval, "-s", "50", "-p", keyval], check=False)
    launched = None
    for _ in range(20):
        time.sleep(0.3)
        new = {pid for pid, cmd in procs().items() if matches(cmd, patterns)} - before
        if new:
            launched = new
            break
    if launched:
        print(f"   OK: processo novo {sorted(launched)} -> {slot} abriu o app")
        return 0
    print("   nada novo apareceu. Causas comuns: app já estava aberto, atalho não"
          "\n   registrado, ou o GNOME não recarregou o grab (relogue).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
