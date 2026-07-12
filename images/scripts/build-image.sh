#!/bin/sh
set -eu

usage() {
    echo "usage: build-image.sh IMAGE_ID SOURCE_FILE OUTPUT_DIRECTORY [SOURCE_SIGNATURE]" >&2
    exit 64
}

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
    usage
fi
image_id=$1
source_file=$2
output_directory=$3
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
images_directory=$(dirname "$script_directory")
repository_directory=$(dirname "$images_directory")
: "${SOURCE_DATE_EPOCH:=1782604800}"
export SOURCE_DATE_EPOCH

python3 "$script_directory/imagectl.py" validate
[ "${FORGE_BUILD_MODE:-development}" != release ] || \
    python3 "$script_directory/imagectl.py" release-check "$image_id"
if [ "$#" -eq 4 ]; then
    python3 "$script_directory/imagectl.py" verify "$image_id" "$source_file" --signature "$4"
else
    python3 "$script_directory/imagectl.py" verify "$image_id" "$source_file"
fi
mkdir -p "$output_directory"

require() { command -v "$1" >/dev/null 2>&1 || { echo "missing tool: $1" >&2; exit 69; }; }
cleanup() { [ -z "${temporary_directory:-}" ] || rm -rf "$temporary_directory"; }
trap cleanup EXIT INT TERM
temporary_directory=$(mktemp -d "${TMPDIR:-/tmp}/forge-image.XXXXXX")
tool_source_directory=${FORGE_TOOL_SOURCE_DIR:-$(dirname "$source_file")}
sh "$script_directory/prepare-tools.sh" "$tool_source_directory" "$temporary_directory/tools"

case "$image_id" in
    ubuntu-seed|manjaro-arm)
        require qemu-img
        require virt-customize
        raw_source=$source_file
        if [ "$image_id" = manjaro-arm ]; then
            require xz
            raw_source=$temporary_directory/manjaro.img
            xz --decompress --stdout "$source_file" > "$raw_source"
        fi
        output=$output_directory/$image_id.qcow2
        qemu-img convert -f "$(qemu-img info --output=json "$raw_source" | python3 -c 'import json,sys; print(json.load(sys.stdin)["format"])')" \
            -O qcow2 -o compat=1.1,lazy_refcounts=on "$raw_source" "$output"
        qemu-img resize "$output" 64G
        provisioner=$([ "$image_id" = ubuntu-seed ] && echo ubuntu || echo manjaro)
        build_environment="FORGE_BUILD_MODE=${FORGE_BUILD_MODE:-development} FORGE_RUSTUP_INIT=/opt/forge-build/tools/rustup-init FORGE_RUSTUP_INIT_SHA256=9732d6c5e2a098d3521fca8145d826ae0aaa067ef2385ead08e6feac88fa5792 FORGE_UV_BINARY=/opt/forge-build/tools/uv FORGE_UV_BINARY_SHA256=b9f74e398b6b15826a4b68b5a83d039036d47df64013e7faf1a9974ec199c144"
        [ "$image_id" != manjaro-arm ] || build_environment="$build_environment FORGE_MANJARO_SNAPSHOT_ACK=1"
        virt-customize -a "$output" --arch aarch64 --network \
            --copy-in "$repository_directory/guest:/opt/forge-build" \
            --copy-in "$images_directory/packs:/opt/forge-build" \
            --copy-in "$images_directory/provision:/opt/forge-build" \
            --copy-in "$temporary_directory/tools:/opt/forge-build" \
            --run-command "$build_environment sh /opt/forge-build/provision/$provisioner.sh"
        qemu-img check "$output"
        ;;
    archlinuxarm-lxc)
        require systemd-nspawn
        require tar
        rootfs=$temporary_directory/rootfs
        mkdir -p "$rootfs/opt/forge-build"
        tar --extract --gzip --numeric-owner --file "$source_file" --directory "$rootfs"
        cp -a "$repository_directory/guest" "$rootfs/opt/forge-build/guest"
        cp -a "$images_directory/packs" "$rootfs/opt/forge-build/packs"
        cp -a "$images_directory/provision" "$rootfs/opt/forge-build/provision"
        cp -a "$temporary_directory/tools" "$rootfs/opt/forge-build/tools"
        systemd-nspawn --directory "$rootfs" --as-pid2 \
            --setenv=FORGE_RUSTUP_INIT=/opt/forge-build/tools/rustup-init \
            --setenv=FORGE_RUSTUP_INIT_SHA256=9732d6c5e2a098d3521fca8145d826ae0aaa067ef2385ead08e6feac88fa5792 \
            --setenv=FORGE_UV_BINARY=/opt/forge-build/tools/uv \
            --setenv=FORGE_UV_BINARY_SHA256=b9f74e398b6b15826a4b68b5a83d039036d47df64013e7faf1a9974ec199c144 \
            /bin/sh /opt/forge-build/provision/arch-lxc.sh
        tar --create --zstd --numeric-owner --sort=name --mtime="@$SOURCE_DATE_EPOCH" \
            --file "$output_directory/$image_id.tar.zst" --directory "$rootfs" .
        ;;
    *)
        echo "unknown image: $image_id" >&2
        exit 64
        ;;
esac
