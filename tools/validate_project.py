#!/usr/bin/env python3
from __future__ import annotations

import plistlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    required = [
        ROOT / "Package.swift",
        ROOT / "xtool.yml",
        ROOT / "Config/ForgeInfo.plist",
        ROOT / "Resources/AppIcon.png",
        ROOT / "Forge/App/ForgeApp.swift",
        ROOT / "ForgeCore/ForgeJob.swift",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"missing required files: {', '.join(missing)}")
    with (ROOT / "Config/ForgeInfo.plist").open("rb") as handle:
        info = plistlib.load(handle)
    assert info["UIApplicationSupportsMultipleScenes"] is True
    assert info["UIFileSharingEnabled"] is True
    assert info["LSSupportsOpeningDocumentsInPlace"] is True
    assert info["ForgeArtifactVariant"] == "thin"
    assert int(info["CFBundleVersion"]) >= 3 and int(info["CFBundleVersion"]) % 2 == 1
    assert "com.bitloop.forge.continued.*" in info["BGTaskSchedulerPermittedIdentifiers"]
    package = (ROOT / "Package.swift").read_text()
    assert '.iOS("27.0")' in package
    assert 'swiftLanguageModes: [.v5]' in package
    print("Forge project contract is valid")


if __name__ == "__main__":
    main()
