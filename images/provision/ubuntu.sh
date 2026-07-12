#!/bin/sh
set -eu
# Installed image path; common.sh is validated separately.
# shellcheck disable=SC1091
. /opt/forge-build/provision/common.sh

set -- core cpp rust python containers lxc xfce
require_root
configure_identity forge-ubuntu
configure_forge_user
install_payload
install_packs apt "$@"
install_optional_toolchains
install_pack_tools apt "$@"
systemctl enable ssh.service forge-guest-agent.service
write_receipt ubuntu "$@"
apt-get clean
rm -rf /var/lib/apt/lists/*
seal_image
