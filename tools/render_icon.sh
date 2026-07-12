#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
input="$ROOT/Resources/ForgeIcon.svg"
output="$ROOT/Resources/AppIcon.png"

if command -v magick >/dev/null 2>&1; then
    magick -background none "$input" -resize 1024x1024 "$output"
elif command -v convert >/dev/null 2>&1; then
    convert -background none "$input" -resize 1024x1024 "$output"
else
    printf 'ImageMagick is required to render the app icon.\n' >&2
    exit 1
fi

python3 - "$output" <<'PY'
from PIL import Image
import sys
image = Image.open(sys.argv[1])
if image.size != (1024, 1024):
    raise SystemExit(f"unexpected icon size: {image.size}")
PY
