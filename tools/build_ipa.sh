#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TOOLCHAIN_ROOT=${DYNALAN_IOS_TOOLCHAIN_ROOT:-"${XDG_DATA_HOME:-$HOME/.local/share}/dynalan-ios"}
SWIFT_ROOT="$TOOLCHAIN_ROOT/swift-current/usr"
XTOOL="$TOOLCHAIN_ROOT/bin/xtool"
VARIANT=thin

while (($#)); do
    case "$1" in
        --variant) VARIANT=${2:?missing variant}; shift 2 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

[[ $VARIANT == thin ]] || {
    printf 'Seed assembly is performed by release/forge-release after a thin build.\n' >&2
    exit 2
}
[[ -x $XTOOL ]] || { printf 'xtool is missing: %s\n' "$XTOOL" >&2; exit 1; }
[[ -x $SWIFT_ROOT/bin/swift ]] || { printf 'Swift toolchain is missing.\n' >&2; exit 1; }
[[ -f $ROOT/Resources/AppIcon.png ]] || { printf 'App icon is missing; run tools/render_icon.sh.\n' >&2; exit 1; }

version=$(
    python3 - "$ROOT/Config/ForgeInfo.plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], "rb") as handle:
    info = plistlib.load(handle)
print(f"{info['CFBundleShortVersionString']}-{info['CFBundleVersion']}")
PY
)
output="$ROOT/build/releases/Forge-${version}-thin.ipa"
mkdir -p "$ROOT/build/releases" "$TOOLCHAIN_ROOT/home"
rm -f "$ROOT/xtool/ForgeForiPad.ipa"

(
    cd "$ROOT"
    export HOME="$TOOLCHAIN_ROOT/home"
    export PATH="$SWIFT_ROOT/bin:$TOOLCHAIN_ROOT/bin:$PATH"
    export XDG_CACHE_HOME="$TOOLCHAIN_ROOT/cache"
    export XDG_CONFIG_HOME="$TOOLCHAIN_ROOT/config"
    export DEVELOPER_DIR="$TOOLCHAIN_ROOT/config/swiftpm/swift-sdks/darwin.artifactbundle/Developer"
    export SWIFTPM_CUSTOM_BIN_DIR="$TOOLCHAIN_ROOT/swiftpm-native-bin"
    export LD_LIBRARY_PATH="$TOOLCHAIN_ROOT/compat/lib:$TOOLCHAIN_ROOT/ldc-1.34.0/lib:${LD_LIBRARY_PATH:-}"
    "$XTOOL" dev build --configuration release --ipa
)

source_ipa="$ROOT/xtool/ForgeForiPad.ipa"
[[ -f $source_ipa ]] || { printf 'xtool produced no IPA.\n' >&2; exit 1; }
install -m 0644 "$source_ipa" "$output"
python3 "$ROOT/tools/validate_ipa.py" "$output"
(cd "$(dirname "$output")" && sha256sum "$(basename "$output")" >"$(basename "$output").sha256")
printf '%s\n' "$output"
