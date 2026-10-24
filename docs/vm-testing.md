# Testing in a VM (virt-manager) — Debian, Ubuntu and KDE

Goal: validate the install on Debian/Ubuntu without touching the main system.
The VM covers the whole flow, including the physical key, if you redirect the
keyboard (section 5).

---

## 1. Create the VM

In virt-manager: **New VM** → *Local install media* → pick the ISO, use
**UEFI** firmware and the `default` NAT network.

| Distro | Recommended ISO | Note |
|---|---|---|
| Ubuntu | 24.04.x LTS Desktop (amd64) | ships GNOME/Wayland and the Firefox snap |
| Debian GNOME | 13 "trixie" (live or netinst + GNOME task) | netinst: tick **GNOME** in tasksel; leave the root password empty so the user gets `sudo` |
| Debian KDE | 13 "trixie" KDE live | Plasma 6.3.6; there is no GNOME shortcut schema |

- 4 GB RAM, 25 GB disk, UEFI firmware (6 GB / 30 GB for the Ubuntu live
  installer).
- Log in to the **Wayland** session — the default on all three.
- Do **not** redirect the keyboard yet (section 5).

---

## 2. Dependencies inside the VM

**Ubuntu:**

```bash
sudo apt update
sudo apt install -y python3 libglib2.0-bin xdg-utils desktop-file-utils zenity wtype
sudo snap install chromium
```

> The Launcher requires a Chromium/Chrome browser. **Firefox does not work**
> (it blocks `ws://` from an https page).

**Debian:**

```bash
sudo apt update
sudo apt install -y python3 libglib2.0-bin xdg-utils desktop-file-utils zenity wtype chromium
```

Verified against the repositories: Debian 13 has `wtype 0.4-3` and `chromium`;
Ubuntu 24.04 has `wtype` in universe and `chromium-browser` as the snap wrapper.

---

## 3. Get the code into the VM

libvirt's `default` network uses NAT: the host is the gateway, normally
`192.168.122.1` (`ip -brief addr show virbr0`).

On the **host**:

```bash
cd ~/development/keychron-assist-linux
tar czf /tmp/kassist.tar.gz .
python3 -m http.server 8000 --bind 192.168.122.1 --directory /tmp
```

In the **VM**:

```bash
mkdir -p ~/kassist && cd ~/kassist
curl -fLo kassist.tar.gz http://192.168.122.1:8000/kassist.tar.gz
tar xzf kassist.tar.gz
```

If `192.168.122.1` does not answer, find the gateway with
`ip route | grep default` — the host is that address.

---

## 4. Install

```bash
cd ~/kassist
./install.sh
```

No `sudo`. The installer copies the binary, creates the per-user
`kassist.service`, registers the `launcherassist://` handler and the GNOME
shortcuts, and finishes by running `doctor`.

On a desktop without the GNOME media-keys schema (KDE Plasma, sway, ...) the
installer automatically falls back to the evdev backend instead of failing;
finish with `sudo ./install.sh --rules` to grant keyboard access.

### WebHID permission (once, with sudo)

The **daemon** needs no root, but the **site** needs access to `/dev/hidraw*`
to write the keycode into the keyboard. On the Fedora host that already existed
(QMK/VIA rules). On a clean VM:

```bash
sudo ./install.sh --rules
```

This installs only `/etc/udev/rules.d/99-keychron-assist.rules` and reloads
udev — the backend stays GNOME. The output should show
`ok: N of M hidraw node(s) accessible`. If the keyboard is not redirected yet,
the warning is expected and goes away after section 5.

### Slots on this distro

Debian 13 and Ubuntu 24.04 ship `xkeyboard-config` 2.42, which does **not**
define `<FK19>`. GNOME mode has **3 slots** (F16–F18) here, versus 4 on Fedora.
The installer prints `F19: no keyval in XKB, skipping` — that is expected. For
all 5 slots on this distro you would need `./install.sh --udev` (sudo). Use
slots 1–3 in the test.

---

## 5. End-to-end test (keyboard redirected)

virt-manager: **Virtual Machine → Redirect USB Device** → select the Keychron.
Put the keyboard in **Cable** mode first (Bluetooth does not show up as USB).

> The keyboard now belongs to the VM; the host loses it while it is redirected.
> The mouse stays on the host, so you can switch it back from the virt-manager
> window. If you have a second USB keyboard, use it on the host so you are never
> locked out.

In the VM:

1. Open **Chromium** at <https://launcher.keychron.com>.
2. **Quick Start → Launch APP** → the app list should appear (100+).
3. Pick an app (e.g. Calculator), pick one of the slots and save.
4. Check the binding:

   ```bash
   python3 tools/selftest.py | grep bindings
   ```

5. **Press the physical key.** The app should open.

If the site cannot save (or does not see the keyboard), check the hidraw rule
from section 4 and replug the keyboard.

---

## 6. Test without a keyboard (simulation)

Useful to validate the compositor chain when you do not want to redirect
hardware. Without a keyboard the site writes nothing, so copy the binding from
the host:

```bash
# on the host
cat ~/.config/keychron-assist-linux/binds.json
# in the VM
mkdir -p ~/.config/keychron-assist-linux
# paste the JSON into binds.json
systemctl --user restart kassist
```

GNOME `.desktop` paths match between Fedora and Debian/Ubuntu
(`/usr/share/applications/org.gnome.Calculator.desktop`). Check with `test -f`
before relying on it.

Close the app (otherwise `gio launch` only focuses the existing window) and
simulate:

```bash
python3 tools/selftest.py --simulate F16
```

The command injects the slot's real keyval (`XF86Launch7`) with `wtype` and
checks whether a new process appeared:

```
-> simulating F16 (XF86Launch7) with wtype...
   OK: new process [12345] -> F16 opened the app
```

---

## 7. What each test covers

| Step | `--simulate` | Physical key |
|---|---|---|
| Site writes the keycode into the keyboard | no | yes (needs the hidraw rule) |
| Firmware emits F16 (keycode 194) | no | yes |
| XKB translates it to `XF86Launch7` | yes | yes |
| GNOME shortcut fires | yes | yes |
| `kassist trigger` → `gio launch` | yes | yes |

---

## 8. If something fails

```bash
python3 tools/selftest.py
systemctl --user status kassist
journalctl --user -u kassist -n 50
ss -ltnp | grep 40003
xdg-mime query default x-scheme-handler/launcherassist
ls -l /dev/hidraw*
```

- **The list appears, but saving a binding writes nothing**: the hidraw rule is
  missing (section 4). `sudo ./install.sh --rules` and replug the keyboard.
- **The shortcut does not fire right after installing**: log out and back in
  (`gsd-media-keys` reloads the grabs on login).
- **Empty app list**: `tools/selftest.py` shows how many apps were found; check
  `XDG_DATA_DIRS`.
- **"Open" on the site does nothing**: test `xdg-open 'launcherassist://x'` in
  the VM. On Ubuntu with the Chromium snap the request goes through the portal;
  if it fails, attach the output of
  `journalctl --user -b | grep -i launcherassist`.
- **Port already in use**: `ss -ltnp | grep 40003` (may be an old instance).

---

## 9. Collect the result

```bash
python3 tools/selftest.py > ~/selftest.txt 2>&1
sed -n '1,30p' ~/selftest.txt
```

Paste the output — that is enough to close the distro matrix.

---

## 10. Undo

```bash
./install.sh --uninstall    # binary, service, handler and shortcuts
```

The udev rule (if installed) and the bindings are kept; the uninstaller prints
the command to remove the rule.
