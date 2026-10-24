# Keychron Assist for Linux (unofficial)

[![CI](https://github.com/ConradoAlmeida/Keychron-Assist-Linux/actions/workflows/ci.yml/badge.svg)](https://github.com/ConradoAlmeida/Keychron-Assist-Linux/actions/workflows/ci.yml)

The Keychron Launcher (`https://launcher.keychron.com`) has a feature called
**Quick Start → Launch APP** that opens programs with a key on the keyboard.
On Windows and macOS that feature relies on the **Keychron Assist** utility,
which does not exist for Linux. This project is a free replacement.

Project status and handoff notes (what works, what is left, decisions and
gotchas): [`docs/status.md`](docs/status.md).

Status: **works, no root** on Fedora 44 (GNOME 50, Wayland). App list, search,
icons, binding writes and triggering from the physical key have been verified.
Debian/Ubuntu: packages and keymap checked; the graphical-session test is
pending (guide in [`docs/vm-testing.md`](docs/vm-testing.md)).

| distro | GNOME/Wayland | usable slots (GNOME mode) | state |
|---|---|---|---|
| Fedora 44 | 50.6 | **4** (F16–F19) | tested with the real keyboard |
| Debian 13 | 48 | **3** (F16–F18) | packages and XKB checked |
| Ubuntu 24.04 LTS | 46 | **3** (F16–F18) | packages and XKB checked |

The number of slots depends on `xkeyboard-config` (see below), not on this
project.

## How the Launcher talks to the Assist

Everything was discovered by reading the site's JS bundle (`main.*.js`). The
protocol is simple and unauthenticated:

```
ws://localhost:40003
```

Every message is JSON:

```json
{"evt_type": "QuickStart" | "Common" | "SysCmd",
 "evt_data": {"cmd": "<command>", "data": <payload>}}
```

| command | direction | expected reply |
|---|---|---|
| `GetVersion` (Common) | site → app | `{"cmd":"GetVersion","data":{"version":"1.1.6"}}` |
| `Get` | site → app | streaming: `FetchStart{count}`, N × `Fetch{data:{name,path,icon,icon_path}}`, `FetchEnd` |
| `AddKeyBind` / `UpdateKeyBind` | site → app | `"Success"` |
| `RemoveKeyBind` | site → app | `"Success"` |
| `GetKeyBinds` | site → app | `[{key, opt_type, opt_data:{name,path,icon,icon_path}}]` |
| `ClearKeyBind` | site → app | `"Success"` |
| `SpawnAppByPath` (SysCmd) | site → app | `true`/`false` |
| `AddApp` / `RemoveApp` / `SelectAppByNative` | site → app | `"Success"` / app list |
| `SetReceiverVpidMap` / `ClearReceiverVpidMap` | site → app | ignored on Linux |

The site also creates a hidden iframe pointing at
`launcherassist://?background=1` to wake the local helper — that is the scheme
the installer registers.

### The detail that makes it possible

A quick-start "slot" **is a plain QMK keycode** that the site writes into the
keymap over WebHID. The site uses:

- the first 5 slots: `KC_F16` … `KC_F20`;
- further slots (up to 50): vendor keycodes (`0x7F00 + i`, usage `0x1CC + i`),
  used when the firmware reports support for more than 5 slots.

Because F16–F20 are standard HID keys, Linux can capture them in two ways —
neither needs to decode the keyboard's vendor reports.

## How the key reaches the app: two modes

**GNOME mode (default, no sudo).** GNOME itself grabs the keys as custom
shortcuts and runs `kassist trigger F16`. Nothing reads `/dev/input`: the
compositor already receives the keys and is allowed to.

Mind the XKB mapping: the `inet` section of the keymap remaps F13..F20, so a
key programmed as `F16` reaches the compositor as `XF86Launch7`. The installer
reads `/usr/share/X11/xkb/symbols/inet` and registers the shortcut with the
**real** keyval — that is what makes it work.

| slot | keycode | keyval (xkeyboard-config ≥ 2.47) | keyval (2.42: Debian 13 / Ubuntu 24.04) |
|---|---|---|---|
| F16 | 194 | `XF86Launch7` | `XF86Launch7` |
| F17 | 195 | `XF86Launch8` | `XF86Launch8` |
| F18 | 196 | `XF86Launch9` | `XF86Launch9` |
| F19 | 197 | `F19` | **not defined** → slot unusable |
| F20 | 198 | `XF86AudioMicMute` | `XF86AudioMicMute` → clashes with mic mute |

That is why the installer **detects** what the system offers and registers only
the possible slots instead of assuming five. `XF86AudioMicMute` is always
skipped so it does not steal the system shortcut.

**udev mode (`./install.sh --udev`, asks for sudo).** The daemon reads
`/dev/input/event*` directly. It captures **all 5 slots** (F16–F20, no XKB
dependency) and is the path for the vendor keycodes (`A6`..`A55`). It needs the
udev rule because the kernel denies access to keyboards on purpose (keylogger
protection).

On desktops without the GNOME media-keys schema (KDE Plasma, sway, ...)
`./install.sh` automatically falls back to the evdev backend instead of failing;
finish with `sudo ./install.sh --rules` so the daemon can read the keyboard.

## Installation (no sudo)

```bash
./install.sh
```

The script:

1. copies `kassist.py` to `~/.local/bin/kassist`;
2. installs and enables the `kassist.service` user unit;
3. registers the `launcherassist://` handler (`~/.local/share/applications`);
4. registers the GNOME shortcuts running `kassist trigger <slot>` (preserves
   existing shortcuts and skips slots without a keyval);
5. warns if the site cannot reach the keyboard yet.

Dependencies: `python3`, `gsettings`/`gio` (`libglib2.0-bin`), `xdg-mime`
(`xdg-utils`). The script prints the right `apt`/`dnf`/`pacman` command when
something is missing.

**One exception to "no sudo":** the *daemon* needs no root, but the *site*
needs access to `/dev/hidraw*` to write the keycode (WebHID). If you never
installed the QMK/VIA rules, run once:

```bash
sudo ./install.sh --rules      # udev rule only, keeps the GNOME backend
```

Without it the app list works, but saving a binding has no effect.

To undo:

```bash
./install.sh --uninstall          # binary, service, handler and shortcuts
```

Bindings are kept in `~/.config/keychron-assist-linux/binds.json`.

Then open `https://launcher.keychron.com` in a **Chromium/Chrome** browser
(Firefox blocks `ws://` from an https page), connect the keyboard over USB and
go to **Quick Start**.

On Ubuntu, use the Google Chrome `.deb`: the Chromium snap cannot open
`/dev/hidraw` (no `hidraw` slot), so WebHID cannot program the key. When only
a confined browser is found, `./install.sh` offers to install Chrome; run
`./install.sh --browser` to do it non-interactively.

### Debian / Ubuntu package

```bash
sudo apt install ./kassist_0.1.0_all.deb
kassist-setup        # once per user: user unit, shortcuts and URI handler
```

The package installs `/usr/bin/kassist`, the user unit in
`/usr/lib/systemd/user`, the `launcherassist://` handler and the udev rule.
`kassist-setup` runs as the desktop user, enables the service and registers the
GNOME shortcuts (or reports that the evdev backend will be used). The unit
runs `kassist run`; the daemon picks the backend at startup.

### Fedora

```bash
sudo dnf install ./kassist-0.1.0-1.fc44.noarch.rpm
kassist-setup
```

The spec is in `packaging/kassist.spec` (COPR-ready) and builds the same
layout: `/usr/bin/kassist`, `kassist-setup`, the user unit, the handler and
the udev rule.

## Commands

```bash
kassist run                     # service in the foreground (default with no args)
kassist trigger F16             # open the app bound to a slot (used by shortcuts)
kassist doctor                  # diagnose install/service/shortcuts/bindings
kassist setup-shortcuts         # (re)register the GNOME shortcuts
kassist remove-shortcuts        # remove the GNOME shortcuts
kassist list-apps               # list discovered apps
kassist run --watch --sniff     # udev mode: show raw keyboard events
kassist run --no-icons          # app list without icons (faster)
kassist protocol 'launcherassist://?background=1'   # URI handler
```

Without installing anything:

```bash
python3 kassist.py list-apps
python3 tests/smoke.py                 # protocol, no browser
python3 tools/selftest.py              # environment + installation
python3 tools/selftest.py --simulate   # inject the key (wtype; keysim on GNOME)
```

## Testing

```bash
bash tests/ci.sh                       # headless: starts its own daemon, no GUI
python3 tests/smoke.py                 # protocol, against a running service
python3 tools/selftest.py              # environment + installation diagnostics
python3 tools/selftest.py --simulate F16   # inject the key (wtype; keysim on GNOME)
```

`tests/ci.sh` is what CI runs: it sets up a throwaway `XDG_CONFIG_HOME`/
`XDG_DATA_HOME`, writes a fixture `.desktop`, starts the daemon on a spare port
and runs the protocol test. CI also checks syntax on Python 3.9/3.11/3.13,
lints the shell scripts, and repeats the protocol test inside Debian 13 and
Ubuntu 24.04 containers. GNOME/Wayland/hardware are covered by the VM guide
instead.

`--simulate` uses `wtype` where the compositor exposes
`zwp_virtual_keyboard_v1` (sway/wlroots, KDE). GNOME/Mutter does not, so the
selftest falls back to `tools/keysim.py` via uinput (asks for sudo; needs
`python3-evdev`).

## Security

The service listens on `127.0.0.1` only. In addition:

- **Origin**: WebSocket connections whose `Origin` header is another site are
  refused with `403` — a malicious page open in the browser cannot talk to the
  daemon (that was the real risk, since any page can reach `localhost`).
- **Execution**: `SpawnAppByPath` only opens a `.desktop` the site listed or one
  inside an application directory; `AddKeyBind` only accepts `App` (`.desktop`)
  or `Url` (`http`/`https`). No arbitrary path execution.

## Limitations

- **Slots.** F16–F20 are the set the firmware exposes when it does not announce
  the 50 extra ones. GNOME mode has 3 or 4 slots (table above); udev mode has
  all 5. Slots `A6`..`A55` use vendor keycodes GNOME cannot express; they would
  need udev mode plus mapping through `--sniff`.
- **The key is not really "F16".** F16/F17/F18 arrive as `XF86Launch7/8/9`. If
  you create shortcuts by hand in GNOME, use those names.
- **One shortcut per slot.** GNOME shortcuts are global: the key opens the app
  regardless of the active keyboard profile/layer.
- **2.4 GHz receiver** (`SetReceiverVpidMap`) is ignored; the original Assist's
  keyboard↔receiver pairing is not replicated. Use the cable.
- **Unofficial.** The protocol may change without notice in any Launcher
  update. `kassist doctor` and `tools/selftest.py` help to locate the break.
- **Privacy (udev mode).** The udev rule gives your user read access to the
  keyboard (like `keyd`/`kanata`). GNOME mode has no such rule.

## Repository layout

```
kassist.py                          service + CLI (WS, apps, binds, shortcuts, evdev)
install.sh                          installer (--udev, --rules, --browser, --uninstall)
debian/                             Debian/Ubuntu package (debhelper + kassist-setup)
tests/smoke.py                      protocol test with a raw WS client
tests/ci.sh                         headless test slice used by CI
tools/selftest.py                   environment diagnostics (and --simulate)
tools/keysim.py                     uinput key injector (GNOME --simulate fallback)
tools/keypeek.py                    shows the real keyval the key sends
docs/vm-testing.md                  virt-manager test guide (Debian/Ubuntu/KDE)
docs/status.md                      project status and handoff notes
.github/workflows/ci.yml            CI: protocol, distros, shellcheck, .deb
packaging/kassist.service           systemd --user unit (template)
packaging/launcherassist.desktop    launcherassist:// handler (template)
packaging/99-keychron-assist.rules  udev rule (--udev/--rules only)
```

## Contact

- Bugs and feature requests: [GitHub Issues](https://github.com/ConradoAlmeida/Keychron-Assist-Linux/issues)
- Security reports: **conrado.almeida@outlook.com** (see [`SECURITY.md`](SECURITY.md))
- Other questions: **conrado.almeida@outlook.com**

## License

MIT (see `LICENSE`). Unofficial project, not affiliated with Keychron.
