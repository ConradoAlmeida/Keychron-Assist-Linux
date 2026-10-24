#!/usr/bin/env bash
# Installer for Keychron Assist for Linux (unofficial).
#
# Default mode (no sudo): GNOME grabs F16..F19 and calls `kassist trigger`.
# Alternative mode: ./install.sh --udev  (reads the keyboard through evdev).
#
# Usage:
#   ./install.sh              install/update (no root)
#   ./install.sh --rules      install only the keyboard udev rule (sudo)
#   ./install.sh --udev       evdev backend + udev rule (sudo)
#   ./install.sh --browser    install Google Chrome when only a confined browser exists (sudo)
#   ./install.sh --uninstall  remove the per-user install (keeps bindings)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
BIN="$BIN_DIR/kassist"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

MODE=gnome
ACTION=install
INSTALL_RULES=0
INSTALL_BROWSER=0

usage() {
  cat <<'EOF'
Installer for Keychron Assist for Linux (unofficial).

Default mode (no sudo): GNOME grabs F16..F19 and calls `kassist trigger`.
Alternative mode: ./install.sh --udev  (reads the keyboard through evdev).

Usage:
  ./install.sh              install/update (no root)
  ./install.sh --rules      install only the keyboard udev rule (sudo)
  ./install.sh --udev       evdev backend + udev rule (sudo)
  ./install.sh --browser    install Google Chrome when only a confined browser exists (sudo)
  ./install.sh --uninstall  remove the per-user install (keeps bindings)
EOF
  exit 0
}

die() { echo "error: $*" >&2; exit 1; }

install_browser() {
  # The launcher needs WebHID (/dev/hidraw); snap/flatpak builds are confined.
  # On Debian/Ubuntu the pragmatic unconfined build is Google Chrome (.deb).
  # shellcheck source=/dev/null
  . /etc/os-release 2>/dev/null || true
  case "${ID:-}${ID_LIKE:-}" in
    *debian*|*ubuntu*)
      tmp=$(mktemp --suffix=.deb)
      echo "==> downloading Google Chrome (.deb)"
      if command -v wget >/dev/null 2>&1; then
        wget -qO "$tmp" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb || true
      elif command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$tmp" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb || true
      else
        rm -f "$tmp"
        echo "  install wget or curl, then run: ./install.sh --browser" >&2
        return 1
      fi
      if [ -s "$tmp" ]; then
        sudo apt-get install -y "$tmp"
        rm -f "$tmp"
      else
        rm -f "$tmp"
        echo "  download failed; install Chrome manually" >&2
        return 1
      fi
      ;;
    *fedora*)
      echo "  install an unconfined build with: sudo dnf install -y chromium"
      return 1
      ;;
    *)
      echo "  install an unconfined Chromium/Chrome manually" >&2
      return 1
      ;;
  esac
}

have_gnome_schema() {
  command -v gsettings >/dev/null 2>&1 &&
    gsettings list-schemas 2>/dev/null | grep -qx 'org.gnome.settings-daemon.plugins.media-keys'
}

for arg in "$@"; do
  case "$arg" in
    --udev) MODE=udev; INSTALL_RULES=1 ;;
    --rules) INSTALL_RULES=1 ;;
    --browser) INSTALL_BROWSER=1 ;;
    --uninstall) ACTION=uninstall ;;
    -h|--help) usage ;;
    *) die "unknown argument: $arg" ;;
  esac
done

[ -f "$SRC/kassist.py" ] || die "run this script from inside the repository"

# ------------------------------------------------------------------ uninstall
if [ "$ACTION" = uninstall ]; then
  echo "==> stopping and removing the service"
  systemctl --user disable --now kassist.service 2>/dev/null || true
  rm -f "$UNIT_DIR/kassist.service"
  systemctl --user daemon-reload 2>/dev/null || true
  if [ -x "$BIN" ]; then
    "$BIN" remove-shortcuts || true
  fi
  rm -f "$BIN" "$APP_DIR/launcherassist.desktop"
  update-desktop-database "$APP_DIR" 2>/dev/null || true
  if [ -f /etc/udev/rules.d/99-keychron-assist.rules ]; then
    echo "==> the udev rule is still present; remove it with:"
    echo "    sudo rm /etc/udev/rules.d/99-keychron-assist.rules && sudo udevadm control --reload-rules"
  fi
  echo
  echo "Removed. Your bindings are kept in ${XDG_CONFIG_HOME:-$HOME/.config}/keychron-assist-linux/"
  exit 0
fi

# ---------------------------------------------------------------- dependencies
missing=()
for cmd in python3 gsettings gio xdg-mime; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "missing commands: ${missing[*]}"
  # shellcheck source=/dev/null
  . /etc/os-release 2>/dev/null || true
  case "${ID:-}${ID_LIKE:-}" in
    *debian*|*ubuntu*) echo "  sudo apt install -y python3 libglib2.0-bin xdg-utils desktop-file-utils zenity" ;;
    *fedora*)          echo "  sudo dnf install -y python3 glib2 xdg-utils desktop-file-utils zenity" ;;
    *arch*)            echo "  sudo pacman -S --needed python glib2 xdg-utils desktop-file-utils zenity" ;;
    *)                 echo "  install python3, glib2 (gsettings/gio) and xdg-utils" ;;
  esac
  exit 1
fi

# The launcher needs WebHID (/dev/hidraw). Snap and Flatpak builds are
# confined and cannot open it; point the user to an unconfined build instead
# of letting the site fail later.
browser_ok=0
for b in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge; do
  p=$(command -v "$b" 2>/dev/null) || continue
  case "$p" in
    /snap/*|/var/lib/flatpak/*) ;;
    *) browser_ok=1 ;;
  esac
done
if [ "$browser_ok" = 0 ]; then
  echo "==> the launcher needs an unconfined Chromium/Chrome (WebHID)"
  if [ "$INSTALL_BROWSER" = 1 ]; then
    install_browser || true
  elif [ -t 0 ]; then
    printf "    install Google Chrome now? [Y/n] "
    read -r ans || ans=n
    case "$ans" in
      ""|y|Y|yes|YES) install_browser || true ;;
      *) echo "    skipped; install it later with: ./install.sh --browser" ;;
    esac
  elif command -v snap >/dev/null 2>&1 && snap list chromium >/dev/null 2>&1; then
    echo "    the Chromium snap cannot open /dev/hidraw. Run: ./install.sh --browser"
  else
    echo "    install Chromium or Google Chrome for the site's WebHID support"
  fi
fi

# Without the GNOME media-keys schema the shortcut backend cannot work; use
# evdev instead of failing in setup-shortcuts (KDE, sway, headless, ...).
if [ "$MODE" = gnome ] && ! have_gnome_schema; then
  echo "==> no GNOME shortcut backend on this system; using the evdev backend"
  MODE=udev
fi

# --------------------------------------------------------------------- binary
echo "==> installing $BIN"
mkdir -p "$BIN_DIR" "$UNIT_DIR" "$APP_DIR"
install -m 0755 "$SRC/kassist.py" "$BIN"

# -------------------------------------------------------------------- service
if [ "$MODE" = udev ]; then
  EXEC="$BIN run --watch"
else
  EXEC="$BIN run"
fi

echo "==> user service (mode: $MODE)"
sed "s|@EXEC@|$EXEC|" "$SRC/packaging/kassist.service" > "$UNIT_DIR/kassist.service"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable kassist.service >/dev/null
  systemctl --user restart kassist.service
else
  echo "  systemctl not found; run '$EXEC' manually"
fi

# ---------------------------------------------------------------- URI handler
echo "==> registering the launcherassist:// scheme"
sed "s|@EXEC@|$BIN|" "$SRC/packaging/launcherassist.desktop" > "$APP_DIR/launcherassist.desktop"
update-desktop-database "$APP_DIR" 2>/dev/null || true
xdg-mime default launcherassist.desktop x-scheme-handler/launcherassist

# ------------------------------------------------------------------ udev rule
if [ "$INSTALL_RULES" = 1 ]; then
  echo "==> udev rule (sudo)"
  sudo install -m 0644 "$SRC/packaging/99-keychron-assist.rules" /etc/udev/rules.d/99-keychron-assist.rules
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=input --settle
  sudo udevadm trigger --subsystem-match=hidraw --settle
  echo
  echo "To undo: sudo rm /etc/udev/rules.d/99-keychron-assist.rules && sudo udevadm control --reload-rules"
fi

# -------------------------------------------------------------------- backend
if [ "$MODE" = udev ]; then
  echo "==> evdev backend enabled (5 slots, independent of XKB)"
  if [ "$INSTALL_RULES" = 0 ]; then
    echo "  note: allow the keyboard with: sudo ./install.sh --rules"
  fi
else
  echo "==> registering GNOME shortcuts"
  "$BIN" setup-shortcuts --binary "$BIN"

  if [ "$INSTALL_RULES" = 0 ]; then
    echo "==> site access to the keyboard (WebHID)"
    nodes=0
    writable=0
    for h in /sys/class/hidraw/hidraw*; do
      [ -e "$h" ] || continue
      id=$(sed -n 's/^HID_ID=//p' "$h/device/uevent" 2>/dev/null | head -1)
      vendor=$(printf '%s' "$id" | cut -d: -f2 | sed 's/^0*//')
      if [ "$vendor" = "3434" ]; then
        nodes=$((nodes + 1))
        [ -w "/dev/$(basename "$h")" ] && writable=$((writable + 1))
      fi
    done
    if [ "$nodes" -eq 0 ]; then
      echo "  warning: no Keychron detected (plug it in over USB to configure)"
    elif [ "$writable" -eq 0 ]; then
      echo "  warning: the site has no permission on the keyboard's hidraw."
      echo "           Without it the Launcher cannot write the key. Run: sudo ./install.sh --rules"
    else
      echo "  ok: $writable of $nodes hidraw node(s) accessible"
    fi
  fi

  echo
  echo "To undo the shortcuts: $BIN remove-shortcuts"
fi

echo
echo "==> verification"
"$BIN" doctor || true

echo
echo "Done. Diagnostics and logs:"
echo "  $BIN doctor"
echo "  journalctl --user -u kassist -f"
