#!/bin/sh
set -eu

usage() {
    echo "usage: install-payload.sh GUEST_SOURCE PACKS_SOURCE" >&2
    exit 64
}

[ "$#" -eq 2 ] || usage
[ "$(id -u)" -eq 0 ] || { echo "install-payload.sh: must run as root" >&2; exit 1; }

guest_source=$1
packs_source=$2

install -d -m 0755 /usr/local/lib/forge_guest /usr/local/bin /usr/share/forge/packs
cp -a "$guest_source/python/forge_guest/." /usr/local/lib/forge_guest/
cp -a "$packs_source/." /usr/share/forge/packs/
install -m 0755 "$guest_source/bin/forge-pack" /usr/local/bin/forge-pack
install -m 0755 "$guest_source/bin/forge-guest-health" /usr/local/bin/forge-guest-health
install -m 0755 "$guest_source/bin/forge-guest-bootstrap" /usr/local/bin/forge-guest-bootstrap
install -m 0644 "$guest_source/systemd/forge-guest-agent.service" /etc/systemd/system/forge-guest-agent.service

# The expansion belongs in the installed profile.
# shellcheck disable=SC2016
printf 'export PYTHONPATH=/usr/local/lib${PYTHONPATH:+:$PYTHONPATH}\n' > /etc/profile.d/forge-python.sh
# These expansions belong in the installed profile.
# shellcheck disable=SC2016
printf 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"\nexport FORGE_PYTHON_ENV="$HOME/.venvs/forge-workstation"\n' > /etc/profile.d/forge-path.sh

systemctl daemon-reload 2>/dev/null || true
