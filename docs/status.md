# Project status — handoff note

> Last updated: **2026-10-24**
> Reference machine: Fedora 44 Workstation, GNOME 50.6, Wayland,
> Python 3.14, Keychron V6 Max keyboard (VID `3434`, PID `0960`).

This document exists so the work can be resumed without relying on the chat
history. It records what is ready, what was actually verified, what was only
checked from repositories, and what is left.

---

## 1. One-sentence summary

The **Keychron Launcher** (website) got a local helper for Linux — `kassist` —
with no root: it serves the app list, stores the Quick Start slot bindings and
opens the app when the physical key is pressed, through GNOME custom shortcuts.

**Works today, verified with the real keyboard:** changing apps on the site,
saving the binding and opening the app with the key (F16 → Calculator, then
Mixer, then Calculator again).

---

## 2. How to resume (5 minutes)

```bash
cd ~/development/keychron-assist-linux

git status                              # repo is tracked now
./kassist.py doctor                     # everything should be OK
python3 tools/selftest.py               # 25 checks, 0 failures
systemctl --user status kassist         # service active
journalctl --user -u kassist -n 30      # site connections and launches
```

Quick test without opening the site (uses the saved binding):

```bash
~/.local/bin/kassist trigger F16        # opens the slot 1 app
```

If you change the code, reinstall so the service picks it up (the unit runs the
binary from `~/.local/bin`, not the one in the repo):

```bash
./install.sh
```

---

## 3. What is installed on this machine

Everything is per-user; nothing goes to `/usr` (except the pre-existing udev
rule, which was **not** installed by this project).

| item | path |
|---|---|
| binary | `~/.local/bin/kassist` |
| service (systemd --user, enabled + active) | `~/.config/systemd/user/kassist.service` |
| `launcherassist://` handler | `~/.local/share/applications/launcherassist.desktop` |
| shortcuts F16–F19 | dconf, schema `org.gnome.settings-daemon.plugins.media-keys` |
| bindings | `~/.config/keychron-assist-linux/binds.json` |
| source | `~/development/keychron-assist-linux` |

Current binding: `vp_id 875825504` → **F16 → Calculator**.

Undo:

```bash
./install.sh --uninstall                     # binary, service, handler, shortcuts
# or, without the repo:
~/.local/bin/kassist remove-shortcuts
systemctl --user disable --now kassist
```

Bindings are **not** deleted by `--uninstall` (they stay in `binds.json`).

---

## 4. Architecture (the minimum needed to work on it)

```
Launcher (Chromium)  --ws://127.0.0.1:40003-->  kassist
   │  Get / AddKeyBind / RemoveKeyBind              │
   │  SpawnAppByPath                                 ├─ scans .desktop (106 apps)
   ▼                                                 ├─ binds.json
launcherassist://  ----handler-------------------->  └─ gio launch <app>
   (wakes the daemon if needed)

physical key ──> firmware (KC_F16) ──> kernel (keycode 194)
   ──> XKB (XF86Launch7) ──> GNOME shortcut ──> kassist trigger F16 ──> gio launch
```

- **Server**: hand-written WebSocket (stdlib only), `127.0.0.1:40003` only.
- **Protocol**: JSON `{"evt_type","evt_data":{"cmd","data"}}`, documented in
  `README.md` (full table).
- **`Get`** is streaming: `FetchStart{count}` → N × `Fetch{data}` → `FetchEnd`.
- **`GetVersion`** replies `1.1.6` (what the site expects to enable the
  feature; not this project's version — that one is `0.1.0`).
- **Bindings** are stored per `vp_id` (device identifier) and slot:
  `{"<vp_id>": {"F16": {"opt_type": "App", "opt_data": {...}}}}`.
- **Slots** are plain QMK keycodes (F16–F20), not vendor reports.

---

## 5. Technical findings that must not be lost

### 5.1 XKB renames F13–F20

The `inet` section of the keymap maps F13+ to "system" keyvals. The GNOME
shortcut must be registered with the **real keyval**, not with `F16`:

| slot | evdev keycode | keyval (xkeyboard-config ≥ 2.47) | keyval (2.42: Debian 13 / Ubuntu 24.04) |
|---|---|---|---|
| F16 | 194 | `XF86Launch7` | `XF86Launch7` |
| F17 | 195 | `XF86Launch8` | `XF86Launch8` |
| F18 | 196 | `XF86Launch9` | `XF86Launch9` |
| F19 | 197 | `F19` | **not defined** → slot unusable |
| F20 | 198 | `XF86AudioMicMute` | `XF86AudioMicMute` |

`kassist.py` reads `/usr/share/X11/xkb/symbols/inet` (the `xkb_symbols "evdev"`
block) at install time and registers only what exists, skipping keyvals already
owned by system shortcuts (`XF86AudioMicMute`).

Practical consequence: **Fedora 44 = 4 slots; Debian 13 / Ubuntu 24.04 = 3
slots** in GNOME mode; udev mode = 5 slots on any distro.

### 5.2 The site needs hidraw access (WebHID)

To write the keycode into the keyboard, the browser uses WebHID →
`/dev/hidraw*`. On this machine that already worked because of **pre-existing**
user rules: `/etc/udev/rules.d/50-qmk.rules` and `70-keychron.rules` (MODE 0666
for VID `3434`). On a clean install (VM) without a rule, the site lists the apps
but **does not save** the binding.

The project now ships `packaging/99-keychron-assist.rules` and the installer:

```bash
sudo ./install.sh --rules     # rule only, keeps the GNOME backend
```

`doctor` and `selftest` warn when no hidraw node is writable.

### 5.3 Input permissions on Linux

- `/dev/input/event*` for keyboards is `root:input 660`, **without** uaccess.
- `/dev/hidraw*` only gets an ACL through a udev rule (uaccess).
- That is why the default mode uses **compositor shortcuts**: GNOME reads the
  key and already has permission. `--udev` (sudo) exists for the 5 slots and for
  future vendor keycodes.

### 5.4 WebSocket origins accepted

The daemon accepts these `Origin` values:

- `https://launcher.keychron.com`
- `https://www.launcher.keychron.com`
- `https://launcher.keychron.cn`

No `Origin` (local clients, tests) is accepted; a different origin → `403`.
This closes the real risk: any site open in the browser could talk to
`localhost:40003`. If the site moves to another domain, apps "disappear" and
`journalctl` shows `rejected origin:`.

### 5.5 Restricted execution

`SpawnAppByPath` only opens a `.desktop` the site listed or one inside an
application directory (XDG/Flatpak/snap). `AddKeyBind` only accepts `App`
(existing `.desktop`) or `Url` (`http`/`https`). Previously any path could be
requested.

### 5.6 wtype does not work on GNOME

Mutter does not expose `zwp_virtual_keyboard_v1`, so `wtype` fails with
"Compositor does not support the virtual keyboard protocol". `tools/selftest.py
--simulate` now falls back to `tools/keysim.py`, which emits the key through
`/dev/uinput` (root + `python3-evdev`). Verified in the Debian GNOME VM: uinput
`KEY_F16` → XKB `XF86Launch7` → GNOME shortcut → `kassist trigger F16` →
`gnome-calculator` launched. On wlroots/KDE, `wtype` is used.

### 5.7 Ubuntu's Chromium snap cannot use WebHID

The chromium snap declares a `hidraw` plug, but snapd provides no slot:
`snap connect chromium:hidraw` fails with `snap "snapd" has no "hidraw"
interface slots`, and the browser never opens `/dev/hidraw*`. The site connects
but cannot program the key. Google Chrome from the `.deb` (unconfined) works:
verified on Ubuntu 24.04 — the site saved F16 → Calculator and the physical key
opened it. Use the Chrome `.deb` on Ubuntu until the snap exposes the interface.
`install.sh` detects this case and offers to install Chrome (`--browser`).

---

## 6. What was done in the distribution refactor

`kassist.py` was rewritten (901 lines) keeping the `binds.json` format and the
verified behaviour:

1. **CLI with subcommands**: `run` (default), `trigger`, `doctor`,
   `setup-shortcuts`, `remove-shortcuts`, `list-apps`, `sniff`, `protocol`.
   Compatibility: `--protocol` still works; no arguments means `run`.
2. **Security**: `Origin` validation and an execution allowlist (5.4/5.5).
3. **`doctor`**: python, service/port (3 s boot tolerance), URI handler,
   shortcuts by keyval, binding apps, evdev backend (optional) and hidraw
   (optional).
4. **`install.sh`**: dependency detection with `apt`/`dnf`/`pacman` hints,
   `--udev`, `--rules`, `--uninstall`, automatic final verification, and a
   warning when hidraw is not accessible. `PartOf=graphical-session.target` in
   the unit; handler with an absolute path.
5. **`tools/selftest.py`**: environment report (distro, session, browser,
   shortcuts, bindings, hidraw) and `--simulate SLOT`, which injects the real
   keyval with `wtype` and confirms a new process appeared — tests the
   compositor chain without hardware.
6. **`tests/smoke.py`**: now sends `Origin`, tests rejection of a foreign
   origin, checks `GetKeyBinds` per `vp_id`, refuses an arbitrary
   `SpawnAppByPath` and cleans up the test binding.
7. **Docs**: `README.md` (distro matrix, slots, security, commands),
   `docs/vm-testing.md`, `docs/status.md` and the MIT `LICENSE`.
8. **Removed**: `packaging/gnome-shortcuts.py` (replaced by `kassist
   setup-shortcuts` / `remove-shortcuts`).
9. **CI**: `.github/workflows/ci.yml` + `tests/ci.sh` (headless protocol test,
   Python matrix, distro containers) and the README badge.
10. **Published on GitHub**: <https://github.com/ConradoAlmeida/Keychron-Assist-Linux>
   (two commits: the GitHub-generated `Initial commit` plus the project).
   Everything, including code comments, output messages and docs, is in
   English.

### What was actually verified

- `./install.sh` from scratch → service active, 4 shortcuts, `doctor` 100% OK.
- `tests/smoke.py` against an isolated instance (port 40006, temporary
  `XDG_CONFIG_HOME`): **PASS** on every check, including `403` for a foreign
  origin and `False` for `/usr/bin/true`.
- `tools/selftest.py`: **25 checks, 0 failures**.
- `tests/ci.sh` (the CI slice) in Debian 13, Ubuntu 24.04 and Python 3.9
  containers: **PASS** in all three, plus a clean `shellcheck` run.
- `kassist trigger F16` (the shortcut flow) and `kassist protocol` work.
- `--version`, `--help`, `doctor` right after a service restart (no false
  negative).

- **Three libvirt VMs** (virt-manager, disks under `/mnt/ssd256/VMs`), all
  Wayland with autologin: Debian 13 GNOME, Debian 13 KDE (Plasma 6.3.6) and
  Ubuntu 24.04.
  - `tests/ci.sh` (headless protocol): **PASS** on all three; `selftest
    --simulate`: **0 failures** on all three after install.
  - `./install.sh` on GNOME/Ubuntu: 3 shortcuts, service active, handler and
    port 40003 OK; `doctor`/`selftest` 0 failures.
  - Debian KDE: `./install.sh` (no flags) detects the missing GNOME schema and
    **falls back to the evdev backend** instead of aborting; `doctor`/`selftest`
    report 0 failures.
  - Debian GNOME: compositor chain validated without hardware — uinput
    `KEY_F16` → `XF86Launch7` → shortcut → `kassist trigger F16` →
    `gnome-calculator` (new `tools/keysim.py`, because wtype cannot inject on
    Mutter).
  - Debian GNOME: **real end-to-end** — site connected to the V6 Max (WebHID),
    binding F16 → Calculator saved and the physical key opened the app.
  - Ubuntu 24.04: **real end-to-end** with Google Chrome (.deb): the site saved
    F16 → Calculator and the physical key opened it. The Chromium snap cannot
    do WebHID (section 5.7).
  - Debian KDE (`--udev`): **real end-to-end** — the site saved F16 → KCalc,
    the daemon read evdev and the physical key launched KCalc
    (`launched: gio launch …` in the journal).

### What was only checked from repositories (Podman containers)

- Debian 13: `python3 3.13.5`, `libglib2.0-bin`, `xdg-utils`,
  `desktop-file-utils`, `zenity 4.1.90`, `wtype 0.4-3`, `chromium 156`.
- Ubuntu 24.04: the same, with `chromium-browser` as the snap wrapper.
- XKB: `xkeyboard-config` 2.42 on both → **no** `<FK19>`. Upstream only added
  `<FK19> { [ F19 ] }` in **2.47** (Fedora 44 ships 2.47).

This is **not** a graphical-session test yet. That is exactly what the VM
covers.

---

## 7. Pending work (in suggested order)

### 7.1 Done — graphical test in the VMs

All three guests were validated end-to-end with the physical V6 Max:

| guest | backend | result |
|---|---|---|
| Debian 13 GNOME | GNOME shortcuts | site saved F16 → Calculator; key opened it |
| Ubuntu 24.04 | GNOME shortcuts | site saved F16 → Calculator (Chrome .deb); key opened it |
| Debian 13 KDE | evdev (`--udev`) | site saved F16 → KCalc; key opened it (`launched:` in the journal) |

Notes kept from the run:

- 3 slots in GNOME mode (no `<FK19>`) — expected, not a bug;
- Ubuntu: the Chromium snap cannot open `/dev/hidraw` (5.7) — `install.sh`
  offers the Google Chrome `.deb`;
- after a USB redirection, if the hidraw/evdev ACLs are missing, re-trigger
  udev: `sudo udevadm trigger --action=add --subsystem-match=input --subsystem-match=hidraw`
  (or replug the keyboard).

### 7.2 Packaging

- ~~`.deb`~~ **done**: `debian/` (debhelper, `Architecture: all`) ships
  `/usr/bin/kassist`, the user unit in `/usr/lib/systemd/user`, the URI
  handler, the udev rule and `kassist-setup`. The unit runs `kassist run` and
  the daemon auto-selects GNOME or evdev. Validated on Debian 13 GNOME and KDE;
  CI builds it on every push.
- `.rpm` + COPR (Fedora): spec in `packaging/kassist.spec`; local build
  validated on Fedora 44 (`kassist-0.1.0-1.fc44.noarch.rpm`), deps satisfied and
  `rpm -Uvh --test` clean. COPR publication pending an account.
- `PKGBUILD` (Arch) — a large custom-keyboard audience.
- Flatpak: **not viable** for the daemon (it must launch host apps); document as
  unsupported.
- The `packaging/kassist.service` and `launcherassist.desktop` templates use
  `@EXEC@`; packaging substitutes `kassist`.

### 7.3 Publication

- ~~CI (GitHub Actions)~~ **done**: `.github/workflows/ci.yml` runs
  `compileall` on Python 3.9/3.11/3.13, `shellcheck` on the shell scripts and
  `tests/ci.sh` (headless protocol test) on the runner and inside Debian 13 /
  Ubuntu 24.04 containers. The same commands were validated locally with
  Podman before the first run.
- Add the repository description and topics on GitHub (manual: `gh` is not
  installed): `keychron`, `qmk`, `linux`, `gnome`, `wayland`, `hid`.
- ~~Tag `v0.1.0` and publish a release with the tarball and instructions.~~
  **automated**: `.github/workflows/release.yml` runs on a `v*` tag, builds the
  `.deb` and `.rpm` from the tagged tree and publishes the release with both
  assets plus a generated `SHA256SUMS`. Notes live in
  `docs/release-notes/<tag>.md`; the job checks the tag against
  `debian/changelog` and the RPM spec.
- "Unofficial, not affiliated with Keychron" notice (already in the README).
- If promoting: a short post on r/Keychron and r/linuxquestions; and a technical
  email to Keychron support offering the project as a reference.

### 7.4 Features

- **Vendor slots `A6`..`A55`** (when the firmware announces the 50 extras):
  udev mode + mapping through `--sniff`; the site does not offer them today.
- **2.4 GHz**: `SetReceiverVpidMap`/`ClearReceiverVpidMap` are ignored; cable
  only.
- **F19 on distros with xkeyboard-config < 2.47**: a rootless alternative would
  be an override in `~/.config/xkb`, but that shadows the system file and breaks
  on upgrades — a conscious decision to **not** do it.
- **Flows not exercised against the real site** (review when testing):
  `GetKeyBinds` with `vp_id` (we assume filtering), `UpdateKeyBind` treated as
  `AddKeyBind`, `ClearKeyBind` clearing the whole `vp_id`, `SelectAppByNative`
  (zenity dialog → replies `AddApp`), `AddApp`/`RemoveApp` (reply `Success`
  without persisting).

### 7.5 Robustness / ideas

- `--doctor --json` for issue reports.
- Detect site contract changes (different `GetVersion`, unknown commands) and
  log them instead of ignoring.
- Unit test for the XKB parser with 2.42 and 2.47 fixtures.
- Logging with timestamps/rotation (today it goes to the journal).

---

## 8. Known limitations (for the README/issue tracker)

- 3–4 slots in GNOME mode (5 in udev mode); `A6`+ unsupported.
- One global shortcut per slot (does not follow keyboard profiles/layers).
- 2.4 GHz receiver not mapped.
- Undocumented, unauthenticated protocol; may change without notice.
- udev mode gives the user read access to the keyboard (like `keyd`/`kanata`).
- Firefox does not work (blocks `ws://` on an https page).

---

## 9. Troubleshooting (cheat sheet)

```bash
systemctl --user status kassist
journalctl --user -u kassist -n 50
ss -ltnp | grep 40003
xdg-mime query default x-scheme-handler/launcherassist
gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings
gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kassist-f16/ binding
ls -l /dev/hidraw*; getfacl /dev/hidraw0
xdg-open 'launcherassist://x'          # tests the handler
python3 tests/smoke.py                 # protocol (uses the real service)
```

Careful: do **not** use `pkill -f <pattern>` when the pattern appears in the
command line itself (that killed the shell during testing). To stop a test
instance use `fuser -k 40006/tcp`.

---

## 10. File map

```
kassist.py                          daemon + CLI (stdlib only)
debian/                             Debian packaging (debhelper)
install.sh                          installer (--udev, --rules, --browser)
tests/smoke.py                      protocol test (raw WS client)
tools/selftest.py                   environment diagnostics + --simulate
tools/keysim.py                     uinput key injector (GNOME --simulate)
tools/keypeek.py                    shows the key's keyval/keycode
tests/ci.sh                         headless test slice used by CI
.github/workflows/ci.yml            CI: syntax, shellcheck, protocol, distros, .deb/.rpm
docs/vm-testing.md                  virt-manager guide (Debian/Ubuntu/KDE)
docs/status.md                      this document
README.md                           overview, protocol, distro matrix
SECURITY.md                         security policy and contact
LICENSE                             MIT
packaging/kassist.service           systemd --user unit (template @EXEC@)
packaging/kassist-setup             per-user setup for the packages
packaging/kassist.spec              Fedora RPM spec (COPR-ready)
packaging/launcherassist.desktop    URI handler (template @EXEC@)
packaging/99-keychron-assist.rules  udev rule (uaccess for evdev and hidraw)
```

---

## 11. Commands that summarise the state

```bash
# install/update (no root)
./install.sh
# site permission (WebHID), once
sudo ./install.sh --rules
# 5 slots without XKB (root, optional)
./install.sh --udev

# verification
./kassist.py doctor
python3 tools/selftest.py

# protocol test without a browser
python3 tests/smoke.py

# undo
./install.sh --uninstall
```
