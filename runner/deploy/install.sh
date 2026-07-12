#!/bin/sh
set -eu

PREFIX=${FORGE_INSTALL_PREFIX:-"$HOME/.local/share/forge-runner/app"}
UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
ENV_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/forge-runner
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${FORGE_PYTHON:-python3.13}
DATA_DIR=${FORGE_DATA_DIR:-"$HOME/.local/share/forge-runner/data"}
PORT=${FORGE_PORT:-4778}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    PYTHON=python3
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 13), "Python 3.13+ is required"'
case $PORT in
    ''|*[!0-9]*) printf 'FORGE_PORT must be an integer from 1 through 65535\n' >&2; exit 2 ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    printf 'FORGE_PORT must be an integer from 1 through 65535\n' >&2
    exit 2
fi

mkdir -p "$PREFIX" "$UNIT_DIR" "$ENV_DIR"
"$PYTHON" -m venv "$PREFIX/.venv"
"$PREFIX/.venv/bin/pip" install --upgrade "$SCRIPT_DIR/.."
install -m 0644 "$SCRIPT_DIR/forge-runner.service" "$UNIT_DIR/forge-runner.service"
if [ ! -e "$ENV_DIR/env" ]; then
    umask 077
    printf 'FORGE_DATA_DIR=%s\nFORGE_DEFAULT_IMAGE=%s\n' \
        "$DATA_DIR" 'localhost/forge-workstation:0.1.0' > "$ENV_DIR/env"
elif ! grep -q '^FORGE_DEFAULT_IMAGE=' "$ENV_DIR/env"; then
    printf 'FORGE_DEFAULT_IMAGE=%s\n' 'localhost/forge-workstation:0.1.0' >> "$ENV_DIR/env"
fi
systemctl --user daemon-reload
systemctl --user enable --now forge-runner.service
INSTANCE_ID=$("$PREFIX/.venv/bin/forge-runner-identity" --data-dir "$DATA_DIR")
AVAHI_SERVICE="$ENV_DIR/forge-runner-avahi.service"
sed \
    -e "s/__FORGE_PORT__/$PORT/g" \
    -e "s/__FORGE_INSTANCE_ID__/$INSTANCE_ID/g" \
    "$SCRIPT_DIR/forge-runner-avahi.service" > "$AVAHI_SERVICE"
printf 'Forge Runner installed. Create a pairing code with:\n  %s --data-dir %s\n' \
    "$PREFIX/.venv/bin/forge-runner-pair" "$DATA_DIR"
printf 'Stable runner identity: %s\n' "$INSTANCE_ID"
printf 'Optional Bonjour advertisement:\n  sudo install -m 0644 %s /etc/avahi/services/forge-runner.service\n' \
    "$AVAHI_SERVICE"
printf 'Build the pinned workstation image before submitting jobs; see %s/../image/README.md\n' \
    "$SCRIPT_DIR"
