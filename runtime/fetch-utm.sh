#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
destination=${1:-"$root/Vendor/UTM"}
expected=048ca7498ea3a374439149d51739d94c5300bcda

if [[ -e $destination ]]; then
    printf 'refusing to replace existing path: %s\n' "$destination" >&2
    exit 2
fi

git clone --filter=blob:none --no-checkout https://github.com/utmapp/UTM.git "$destination"
git -C "$destination" checkout --detach "$expected"
actual=$(git -C "$destination" rev-parse HEAD)
[[ $actual == "$expected" ]] || {
    printf 'UTM commit mismatch: expected %s, got %s\n' "$expected" "$actual" >&2
    exit 1
}
git -C "$destination" submodule update --init --recursive --depth 1
printf 'UTM v4.7.5 source is pinned at %s\n' "$destination"
