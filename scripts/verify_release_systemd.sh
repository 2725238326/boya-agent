#!/usr/bin/env bash
# Run only on a disposable Linux host with systemd and passwordless sudo.
# Exercises real service stop/start, git rollback, venv restoration and HTTP;
# never references the production service, credentials, database or host.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d /tmp/boya-release-systemd.XXXXXX)"
UNIT="boya-release-test-$(basename "$SANDBOX").service"
UNIT_PATH="/run/systemd/system/$UNIT"

cleanup() {
  sudo systemctl stop "$UNIT" >/dev/null 2>&1 || true
  sudo rm -f -- "$UNIT_PATH"
  sudo systemctl daemon-reload
  sudo systemctl reset-failed "$UNIT" >/dev/null 2>&1 || true
  # mktemp target is fixed inside /tmp; no production paths are accepted.
  case "$SANDBOX" in /tmp/boya-release-systemd.*) rm -rf -- "$SANDBOX" ;; esac
}
trap cleanup EXIT
chmod 755 "$SANDBOX"
export APP_DIR="$SANDBOX/app"
export RELEASE_RECOVERY_ROOT="$SANDBOX"
export SERVICE_NAME="$UNIT"
mkdir -p "$APP_DIR/venv"
printf 'old\n' > "$APP_DIR/venv/version"
git -C "$APP_DIR" init -q
git -C "$APP_DIR" config user.name "Release fixture"
git -C "$APP_DIR" config user.email "fixture@example.test"
git -C "$APP_DIR" add venv/version
git -C "$APP_DIR" commit -qm "old fixture"
export PREVIOUS_SHA="$(git -C "$APP_DIR" rev-parse HEAD)"
printf 'candidate\n' > "$APP_DIR/candidate"
git -C "$APP_DIR" add candidate
git -C "$APP_DIR" commit -qm "candidate fixture"
PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    print(sock.getsockname()[1])
PY
)"
export LOCAL_HEALTHCHECK_URL="http://127.0.0.1:$PORT/"
cat > "$SANDBOX/unit" <<EOF
[Unit]
Description=Isolated BOYA release recovery fixture
[Service]
Type=simple
ExecStart=$(command -v python3) -m http.server $PORT --bind 127.0.0.1 --directory $SANDBOX
Restart=no
EOF
sudo install -m 644 "$SANDBOX/unit" "$UNIT_PATH"
sudo systemctl daemon-reload
sudo systemctl start "$UNIT"
for attempt in $(seq 1 20); do
  if curl --fail --silent --max-time 2 "$LOCAL_HEALTHCHECK_URL" >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent --max-time 2 "$LOCAL_HEALTHCHECK_URL" >/dev/null

export GUARD_SOURCE="$ROOT/deploy/release_guard.sh"
set +e
bash <<'BASH'
set -euo pipefail
source "$GUARD_SOURCE"
DEPS_CHANGED=true
SERVICE_WAS_ACTIVE=1
release_guard_prepare
trap release_guard_exit EXIT
restore_email_state() { :; }
cleanup_wheelhouse() { :; }
RELEASE_MUTATED=1
sudo systemctl stop "$SERVICE_NAME"
DEPENDENCIES_TOUCHED=1
printf 'broken\n' > "$APP_DIR/venv/version"
# A real failed installation/health command takes the same EXIT trap.
false
BASH
RESULT=$?
set -e
if [ "$RESULT" = "0" ]; then echo "failure injection unexpectedly succeeded"; exit 1; fi
test "$(git -C "$APP_DIR" rev-parse HEAD)" = "$PREVIOUS_SHA"
test "$(cat "$APP_DIR/venv/version")" = "old"
sudo systemctl is-active --quiet "$UNIT"
curl --fail --silent --max-time 3 "$LOCAL_HEALTHCHECK_URL" >/dev/null
echo "PASS isolated systemd recovery: previous git commit, venv restored, actual service active, HTTP available"
