import json
import zipfile
from pathlib import Path

import pytest

from forge_release.cli import main
from forge_release.crypto import load_private_key, load_public_key
from forge_release.recovery import build_recovery_manifest, verify_recovery_manifest
from forge_release.validation import ValidationError, scan_private_material, validate_notice, validate_sbom


def test_recovery_manifest_round_trip(tmp_path: Path, keys):
    _, _, private, public, *_ = keys
    seed = tmp_path / "Forge Seed.ipa"
    notice = tmp_path / "NOTICE"
    seed.write_bytes(b"seed")
    notice.write_text("UTM and QEMU notices")
    manifest = build_recovery_manifest(
        [("seed_ipa", seed), ("notice", notice)],
        release_sequence=1,
        marketing_version="1.0.0",
        private_key=private,
    )
    unsigned = verify_recovery_manifest(manifest, public, tmp_path)
    assert {item["role"] for item in unsigned["files"]} == {"seed_ipa", "notice"}
    seed.write_bytes(b"changed")
    with pytest.raises(ValueError):
        verify_recovery_manifest(manifest, public, tmp_path)


def test_sbom_and_notice_are_strict(tmp_path: Path):
    sbom = tmp_path / "sbom.spdx.json"
    sbom.write_text(json.dumps({
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "packages": [{"name": "QEMU", "licenseConcluded": "GPL-2.0-only", "licenseDeclared": "GPL-2.0-only"}],
    }))
    notice = tmp_path / "NOTICE"
    notice.write_text("Forge includes QEMU.")
    assert validate_sbom(sbom, ["QEMU"]) == {"QEMU"}
    validate_notice(notice, ["QEMU"])
    with pytest.raises(ValidationError):
        validate_notice(notice, ["UTM"])


def test_private_key_scan_checks_plain_and_nested_ipa(tmp_path: Path, keys):
    private_path, *_ = keys
    output = tmp_path / "output"
    output.mkdir()
    (output / "public.txt").write_text("safe")
    scan_private_material(output, private_path)
    ipa = output / "Forge.ipa"
    with zipfile.ZipFile(ipa, "w") as archive:
        archive.writestr("Payload/Forge.app/leak.key", private_path.read_bytes())
    with pytest.raises(ValidationError, match="private key material"):
        scan_private_material(output, private_path)


def test_cli_package_metadata_smoke(tmp_path: Path):
    ipa = tmp_path / "Forge.ipa"
    output = tmp_path / "package.json"
    ipa.write_bytes(b"ipa")
    result = main([
        "package-metadata",
        "--ipa", str(ipa),
        "--kind", "thin",
        "--sequence", "9",
        "--marketing-version", "1.2.3",
        "--release-date", "2026-07-12",
        "--download-url", "https://forge.invalid/Forge.ipa",
        "--output", str(output),
    ])
    assert result == 0
    assert json.loads(output.read_text())["build_number"] == 19
