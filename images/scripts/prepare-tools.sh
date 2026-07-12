#!/bin/sh
set -eu

usage() {
    echo "usage: prepare-tools.sh SOURCE_CACHE STAGING_DIRECTORY" >&2
    exit 64
}

[ "$#" -eq 2 ] || usage
source_cache=$1
staging=$2
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
rustup_source=$source_cache/rustup-init-1.29.0-aarch64
uv_source=$source_cache/uv-0.11.28-aarch64.tar.gz

python3 "$script_directory/imagectl.py" verify-source rustup-init-1.29.0-aarch64 "$rustup_source"
python3 "$script_directory/imagectl.py" verify-source uv-0.11.28-aarch64 "$uv_source"

mkdir -p "$staging"
install -m 0755 "$rustup_source" "$staging/rustup-init"
tar -xzf "$uv_source" -C "$staging" --strip-components=1 uv-aarch64-unknown-linux-gnu/uv
printf '%s  %s\n' \
    b9f74e398b6b15826a4b68b5a83d039036d47df64013e7faf1a9974ec199c144 \
    "$staging/uv" | sha256sum -c - >/dev/null
chmod 0755 "$staging/uv"
