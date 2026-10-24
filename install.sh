#!/usr/bin/env bash
# Instalador do Keychron Assist para Linux (não oficial).
#
# Modo padrão (sem sudo): o GNOME captura F16..F19 e chama `kassist trigger`.
# Modo alternativo: ./install.sh --udev  (lê o teclado via evdev, pede sudo).
#
# Uso:
#   ./install.sh              instala/atualiza (sem root)
#   ./install.sh --rules      instala só a regra udev do teclado (sudo)
#   ./install.sh --udev       backend evdev + regra udev (sudo)
#   ./install.sh --uninstall  remove a instalação do usuário (mantém binds)
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

usage() {
  cat <<'EOF'
Instalador do Keychron Assist para Linux (não oficial).

Modo padrão (sem sudo): o GNOME captura F16..F19 e chama `kassist trigger`.
Modo alternativo: ./install.sh --udev  (lê o teclado via evdev, pede sudo).

Uso:
  ./install.sh              instala/atualiza (sem root)
  ./install.sh --rules      instala só a regra udev do teclado (sudo)
  ./install.sh --udev       backend evdev + regra udev (sudo)
  ./install.sh --uninstall  remove a instalação do usuário (mantém binds)
EOF
  exit 0
}

die() { echo "erro: $*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --udev) MODE=udev; INSTALL_RULES=1 ;;
    --rules) INSTALL_RULES=1 ;;
    --uninstall) ACTION=uninstall ;;
    -h|--help) usage ;;
    *) die "argumento desconhecido: $arg" ;;
  esac
done

[ -f "$SRC/kassist.py" ] || die "rode o script de dentro do repositório"

# ---------------------------------------------------------------- desinstalar
if [ "$ACTION" = uninstall ]; then
  echo "==> parando e removendo o serviço"
  systemctl --user disable --now kassist.service 2>/dev/null || true
  rm -f "$UNIT_DIR/kassist.service"
  systemctl --user daemon-reload 2>/dev/null || true
  if [ -x "$BIN" ]; then
    "$BIN" remove-shortcuts || true
  fi
  rm -f "$BIN" "$APP_DIR/launcherassist.desktop"
  update-desktop-database "$APP_DIR" 2>/dev/null || true
  if [ -f /etc/udev/rules.d/99-keychron-assist.rules ]; then
    echo "==> regra udev ainda presente; remova com:"
    echo "    sudo rm /etc/udev/rules.d/99-keychron-assist.rules && sudo udevadm control --reload-rules"
  fi
  echo
  echo "Removido. Suas bindings continuam em ${XDG_CONFIG_HOME:-$HOME/.config}/keychron-assist-linux/"
  exit 0
fi

# ---------------------------------------------------------------- dependências
missing=()
for cmd in python3 gsettings gio xdg-mime; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "faltam comandos: ${missing[*]}"
  . /etc/os-release 2>/dev/null || true
  case "${ID:-}${ID_LIKE:-}" in
    *debian*|*ubuntu*) echo "  sudo apt install -y python3 libglib2.0-bin xdg-utils desktop-file-utils zenity" ;;
    *fedora*)          echo "  sudo dnf install -y python3 glib2 xdg-utils desktop-file-utils zenity" ;;
    *arch*)            echo "  sudo pacman -S --needed python glib2 xdg-utils desktop-file-utils zenity" ;;
    *)                 echo "  instale python3, glib2 (gsettings/gio), xdg-utils" ;;
  esac
  exit 1
fi

# ---------------------------------------------------------------- binário
echo "==> instalando $BIN"
mkdir -p "$BIN_DIR" "$UNIT_DIR" "$APP_DIR"
install -m 0755 "$SRC/kassist.py" "$BIN"

# ---------------------------------------------------------------- serviço
if [ "$MODE" = udev ]; then
  EXEC="$BIN run --watch"
else
  EXEC="$BIN run"
fi

echo "==> serviço de usuário (modo: $MODE)"
sed "s|@EXEC@|$EXEC|" "$SRC/packaging/kassist.service" > "$UNIT_DIR/kassist.service"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable kassist.service >/dev/null
  systemctl --user restart kassist.service
else
  echo "  systemctl não encontrado; rode '$EXEC' manualmente"
fi

# ---------------------------------------------------------------- handler de URI
echo "==> registrando o esquema launcherassist://"
sed "s|@EXEC@|$BIN|" "$SRC/packaging/launcherassist.desktop" > "$APP_DIR/launcherassist.desktop"
update-desktop-database "$APP_DIR" 2>/dev/null || true
xdg-mime default launcherassist.desktop x-scheme-handler/launcherassist

# ---------------------------------------------------------------- regra udev
if [ "$INSTALL_RULES" = 1 ]; then
  echo "==> regra udev (sudo)"
  sudo install -m 0644 "$SRC/packaging/99-keychron-assist.rules" /etc/udev/rules.d/99-keychron-assist.rules
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=input
  sudo udevadm trigger --subsystem-match=hidraw
  echo
  echo "Para desfazer: sudo rm /etc/udev/rules.d/99-keychron-assist.rules && sudo udevadm control --reload-rules"
fi

# ---------------------------------------------------------------- backend
if [ "$MODE" = udev ]; then
  echo "==> backend evdev habilitado (5 slots, sem XKB)"
else
  case "${XDG_CURRENT_DESKTOP:-}" in
    *GNOME*) ;;
    "") echo "  aviso: sessão gráfica não detectada; atalhos precisam do GNOME" ;;
    *) echo "  aviso: '$XDG_CURRENT_DESKTOP' não é GNOME; use --udev (precisa de sudo) se os atalhos não funcionarem" ;;
  esac
  echo "==> registrando atalhos do GNOME"
  "$BIN" setup-shortcuts --binary "$BIN"

  if [ "$INSTALL_RULES" = 0 ]; then
    echo "==> acesso do site ao teclado (WebHID)"
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
      echo "  aviso: nenhum Keychron detectado (conecte por cabo para configurar)"
    elif [ "$writable" -eq 0 ]; then
      echo "  aviso: o site não tem permissão no hidraw do teclado."
      echo "         Sem isso o Launcher não grava a tecla. Rode: sudo ./install.sh --rules"
    else
      echo "  ok: $writable de $nodes nó(s) hidraw acessíveis"
    fi
  fi
  echo
  echo "Para desfazer os atalhos: $BIN remove-shortcuts"
fi

echo
echo "==> verificação"
"$BIN" doctor || true

echo
echo "Pronto. Diagnóstico e logs:"
echo "  $BIN doctor"
echo "  journalctl --user -u kassist -f"
