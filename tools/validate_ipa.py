#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import plistlib
from pathlib import Path, PurePosixPath
import sys
import zipfile

THIN_LIMIT = 500_000_000
EXPECTED_BUNDLE_ID = "com.bitloop.forge"


def validate(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"IPA is missing: {path}")
    if path.stat().st_size > THIN_LIMIT:
        raise ValueError(f"thin IPA exceeds {THIN_LIMIT} bytes")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        info_names = [name for name in names if PurePosixPath(name).match("Payload/*.app/Info.plist")]
        if len(info_names) != 1:
            raise ValueError("IPA must contain exactly one application Info.plist")
        info = plistlib.loads(archive.read(info_names[0]))
        bundle_id = info.get("CFBundleIdentifier")
        if bundle_id != EXPECTED_BUNDLE_ID:
            raise ValueError(f"unexpected bundle identifier: {bundle_id!r}")
        if info.get("ForgeArtifactVariant") != "thin":
            raise ValueError("thin IPA has the wrong ForgeArtifactVariant")
        try:
            build = int(info["CFBundleVersion"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("thin IPA has an invalid build number") from error
        if build < 3 or build % 2 != 1:
            raise ValueError("thin IPA build number must be the odd member of a Seed/thin pair")
        if info.get("UIApplicationSupportsMultipleScenes") is not True:
            raise ValueError("multiwindow support is missing")
        for name in names:
            lowered = name.lower()
            if "/seedassets/" in lowered:
                raise ValueError("thin IPA contains Seed recovery data")
            if lowered.endswith((".p12", ".mobileprovision.key", "release-ed25519.key")):
                raise ValueError(f"private release material in IPA: {name}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"validated {path.name} ({path.stat().st_size} bytes, sha256={digest})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_ipa.py PATH")
    validate(Path(sys.argv[1]))
