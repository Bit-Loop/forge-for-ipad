#!/bin/sh
set -eu
# Installed image path; common.sh is validated separately.
# shellcheck disable=SC1091
. /opt/forge-build/provision/common.sh

set -- core cpp rust python containers xfce workstation
require_root
[ "${FORGE_MANJARO_SNAPSHOT_ACK:-0}" = 1 ] || \
    die "set FORGE_MANJARO_SNAPSHOT_ACK=1 after pinning and testing an upstream unstable snapshot"
configure_identity forge-manjaro
configure_forge_user
install_payload
install_packs pacman "$@"
install_optional_toolchains
install_pack_tools pacman "$@"
systemctl enable sshd.service forge-guest-agent.service NetworkManager.service lightdm.service
write_receipt manjaro "$@"
pacman -Scc --noconfirm || true
seal_image
