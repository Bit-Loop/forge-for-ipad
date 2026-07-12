#!/bin/sh
set -eu

script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repository_directory=$(dirname "$(dirname "$script_directory")")
cd "$repository_directory"

python3 images/scripts/imagectl.py validate
python3 -m unittest discover -s images/tests -v
python3 -m unittest discover -s guest/tests -v
python3 -m compileall -q images/scripts guest/python
find images guest -type f \( -name '*.sh' -o -path '*/bin/*' \) \
    -print0 | xargs -0 -n1 sh -n

if command -v shellcheck >/dev/null 2>&1; then
    find images guest -type f \( -name '*.sh' -o -path '*/bin/*' \) \
        -print0 | xargs -0 shellcheck
fi
