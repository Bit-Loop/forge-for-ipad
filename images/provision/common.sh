#!/bin/sh
set -eu

: "${FORGE_BUILD_MODE:=development}"
: "${SOURCE_DATE_EPOCH:=1782604800}"
: "${FORGE_BUILD_ROOT:=/opt/forge-build}"

log() { printf 'forge-provision: %s\n' "$*"; }
die() { log "$*" >&2; exit 1; }
require_root() { [ "$(id -u)" -eq 0 ] || die "must run as root"; }

require_release_input() {
    name=$1
    eval "value=\${$name:-}"
    if [ "$FORGE_BUILD_MODE" = release ] && [ -z "$value" ]; then
        die "release build requires $name"
    fi
}

configure_identity() {
    hostname=$1
    printf '%s\n' "$hostname" > /etc/hostname
    ln -snf /usr/share/zoneinfo/UTC /etc/localtime
    printf 'LANG=C.UTF-8\n' > /etc/locale.conf
}

install_payload() {
    sh "$FORGE_BUILD_ROOT/guest/scripts/install-payload.sh" \
        "$FORGE_BUILD_ROOT/guest" "$FORGE_BUILD_ROOT/packs"
}

install_packs() {
    manager=$1
    shift
    PYTHONPATH=/usr/local/lib /usr/local/bin/forge-pack \
        --manager "$manager" --packs-dir /usr/share/forge/packs --install --system-only "$@"
}

install_pack_tools() {
    manager=$1
    shift
    PYTHONPATH=/usr/local/lib /usr/local/bin/forge-pack \
        --manager "$manager" --packs-dir /usr/share/forge/packs --install --tools-only "$@"
}

install_verified_file() {
    source=$1
    checksum=$2
    destination=$3
    [ -f "$source" ] || die "missing locked input: $source"
    printf '%s  %s\n' "$checksum" "$source" | sha256sum -c - >/dev/null
    install -m 0755 "$source" "$destination"
}

install_optional_toolchains() {
    require_release_input FORGE_RUSTUP_INIT
    require_release_input FORGE_RUSTUP_INIT_SHA256
    require_release_input FORGE_UV_BINARY
    require_release_input FORGE_UV_BINARY_SHA256

    if [ -n "${FORGE_RUSTUP_INIT:-}" ]; then
        install_verified_file "$FORGE_RUSTUP_INIT" "$FORGE_RUSTUP_INIT_SHA256" /tmp/rustup-init
        sudo -u forge env HOME=/home/forge RUSTUP_HOME=/home/forge/.rustup \
            CARGO_HOME=/home/forge/.cargo /tmp/rustup-init -y --profile minimal --default-toolchain stable \
            --component clippy,rust-analyzer,rust-src,rustfmt
        rm -f /tmp/rustup-init
    fi

    if [ -n "${FORGE_UV_BINARY:-}" ]; then
        install_verified_file "$FORGE_UV_BINARY" "$FORGE_UV_BINARY_SHA256" /usr/local/bin/uv
        ln -sf uv /usr/local/bin/uvx
    fi
}

configure_forge_user() {
    id forge >/dev/null 2>&1 || useradd --create-home --shell /bin/bash forge
    printf 'forge ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/90-forge
    chmod 0440 /etc/sudoers.d/90-forge
    install -d -o forge -g forge -m 0750 /home/forge/Workspaces /home/forge/.cache/forge
}

write_receipt() {
    distro=$1
    shift
    install -d -m 0755 /usr/share/forge
    packages=$(if command -v dpkg-query >/dev/null 2>&1; then
        dpkg-query -W -f='${Package}=${Version}\n' | LC_ALL=C sort
    else
        pacman -Q | LC_ALL=C sort
    fi)
    {
        printf 'schema=1\n'
        printf 'distro=%s\n' "$distro"
        printf 'source_date_epoch=%s\n' "$SOURCE_DATE_EPOCH"
        printf 'packs=%s\n' "$*"
        printf '%s\n' "$packages"
        if [ -x /usr/local/bin/uv ]; then
            sudo -u forge env HOME=/home/forge UV_TOOL_DIR=/home/forge/.local/share/uv/tools \
                /usr/local/bin/uv tool list 2>/dev/null || true
            sudo -u forge env HOME=/home/forge \
                /usr/local/bin/uv pip freeze --python /home/forge/.venvs/forge-workstation/bin/python \
                2>/dev/null || true
        fi
        if [ -x /home/forge/.cargo/bin/cargo ]; then
            sudo -u forge env HOME=/home/forge CARGO_HOME=/home/forge/.cargo \
                /home/forge/.cargo/bin/cargo install --list 2>/dev/null || true
        fi
    } > /usr/share/forge/image-receipt.txt
}

seal_image() {
    rm -f /etc/machine-id /var/lib/dbus/machine-id
    install -m 0000 /dev/null /etc/machine-id
    rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub
    rm -rf /tmp/* /var/tmp/*
    sync
}
