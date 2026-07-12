#!/bin/sh
set -eu
# Installed image path; common.sh is validated separately.
# shellcheck disable=SC1091
. /opt/forge-build/provision/common.sh

set -- core cpp rust python
require_root
configure_identity forge-arch-lxc
configure_forge_user
install_payload
install_packs pacman "$@"
install_optional_toolchains
install_pack_tools pacman "$@"
systemctl enable sshd.service forge-guest-agent.service
write_receipt archlinuxarm "$@"
pacman -Scc --noconfirm || true
seal_image
