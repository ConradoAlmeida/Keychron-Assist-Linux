#!/usr/bin/env bash
# Headless slice of the test suite: protocol smoke test against a throwaway
# daemon. No GNOME, no Wayland, no keyboard — safe for CI and containers.
#
#   bash tests/ci.sh
#   KASSIST_TEST_PORT=41000 bash tests/ci.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
PORT="${KASSIST_TEST_PORT:-40006}"
PID=""

cleanup() {
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

export XDG_CONFIG_HOME="$WORK/config"
export XDG_DATA_HOME="$WORK/data"
mkdir -p "$XDG_DATA_HOME/applications"

# Fixture app so the list is never empty in a bare container.
cat > "$XDG_DATA_HOME/applications/ci-fixture.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=CI Fixture
Exec=/bin/true
Icon=utilities-terminal
EOF

python3 "$ROOT/kassist.py" --version

python3 "$ROOT/kassist.py" run --port "$PORT" --no-icons &
PID=$!

python3 - "$PORT" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
for _ in range(50):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(0)
    time.sleep(0.1)
raise SystemExit("daemon did not open the port")
PY

python3 "$ROOT/tests/smoke.py" --port "$PORT"
