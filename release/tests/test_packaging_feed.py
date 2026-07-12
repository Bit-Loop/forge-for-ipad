import json
from pathlib import Path
import plistlib
import zipfile

import pytest

from forge_release.assets import build_asset_pack
from forge_release.canonical import write_json
from forge_release.packaging import (
    FORGE_BUNDLE_ID,
    PackageError,
    SEED_BUDGET_BYTES,
    THIN_BUDGET_BYTES,
    assemble_seed_ipa,
    package_metadata,
    paired_build_numbers,
    payload_assembly_metadata,
    validate_package_metadata,
    validate_pair,
    validate_payload_assembly,
)
from forge_release.sidestore import generate_source


def metadata(tmp_path: Path, kind: str, sequence: int = 7):
    ipa = tmp_path / f"Forge-{kind}.ipa"
    ipa.write_bytes(kind.encode() * 10)
    return package_metadata(
        ipa,
        kind=kind,
        sequence=sequence,
        marketing_version="1.0.0",
        release_date="2026-07-12",
        download_url=f"https://forge.invalid/{ipa.name}",
    ), ipa


def staged_seed_assets(tmp_path: Path, keys):
    _, _, private, public, *_ = keys
    source = tmp_path / "seed-source"
    source.mkdir()
    (source / "runtime.bin").write_bytes(b"verified-pack")
    assets = tmp_path / "assets"
    manifest = build_asset_pack(
        source,
        assets / "chunks",
        pack_id="seed-test",
        version="1.0.0",
        minimum_runtime_abi="1",
        licenses=[{"name": "test", "spdx": "Apache-2.0"}],
        compatibility={"architecture": "arm64", "minimum_ipados": "27.0"},
        private_key=private,
    )
    write_json(assets / "manifests" / "seed-test.json", manifest)
    return assets, public


def test_exact_budgets_and_paired_builds(tmp_path: Path):
    assert THIN_BUDGET_BYTES == 500_000_000
    assert SEED_BUDGET_BYTES == 3_800_000_000
    assert paired_build_numbers(7) == {"seed": 14, "thin": 15}
    seed, seed_ipa = metadata(tmp_path, "seed")
    thin, thin_ipa = metadata(tmp_path, "thin")
    validate_package_metadata(seed, seed_ipa)
    validate_package_metadata(thin, thin_ipa)
    validate_pair(seed, thin)
    assert seed["bundle_id"] == thin["bundle_id"] == FORGE_BUNDLE_ID


def test_budget_and_pair_tampering_are_rejected(tmp_path: Path):
    seed, _ = metadata(tmp_path, "seed")
    thin, _ = metadata(tmp_path, "thin")
    thin["budget_bytes"] += 1
    with pytest.raises(PackageError):
        validate_package_metadata(thin)
    thin["budget_bytes"] = THIN_BUDGET_BYTES
    thin["marketing_version"] = "2.0.0"
    with pytest.raises(PackageError):
        validate_pair(seed, thin)


def test_sidestore_feed_orders_seed_then_thin_by_build_number(tmp_path: Path):
    seed, _ = metadata(tmp_path, "seed")
    thin, _ = metadata(tmp_path, "thin")
    source = generate_source(
        [seed, thin],
        source_name="Forge",
        source_identifier="com.bitloop.forge.source",
        subtitle="Forge",
        description="Forge for iPad",
        icon_url="https://forge.invalid/icon.png",
        website="https://github.com/Bit-Loop/forge-for-ipad",
    )
    versions = source["apps"][0]["versions"]
    assert [item["buildVersion"] for item in versions] == ["15", "14"]
    assert [item["size"] for item in versions] == [thin["artifact"]["size"], seed["artifact"]["size"]]


def test_payload_assembly_separates_thin_and_seed_content(tmp_path: Path):
    staging = tmp_path / "staging"
    app = staging / "Payload" / "Forge.app"
    app.mkdir(parents=True)
    (app / "Forge").write_bytes(b"executable")
    thin = payload_assembly_metadata(staging, kind="thin", sequence=3, marketing_version="1.0.0")
    validate_payload_assembly(thin, staging)
    assert thin["build_number"] == 7
    assert {item["role"] for item in thin["files"]} == {"application"}

    seed_assets = app / "SeedAssets"
    seed_assets.mkdir()
    (seed_assets / "ubuntu.manifest.json").write_text("{}")
    with pytest.raises(PackageError):
        payload_assembly_metadata(staging, kind="thin", sequence=3, marketing_version="1.0.0")
    seed = payload_assembly_metadata(staging, kind="seed", sequence=3, marketing_version="1.0.0")
    validate_payload_assembly(seed, staging)
    assert seed["build_number"] == 6
    assert "seed_runtime" in {item["role"] for item in seed["files"]}


def test_seed_assembler_embeds_assets_updates_build_and_removes_signature(tmp_path: Path, keys):
    thin = tmp_path / "Forge-thin.ipa"
    source_info = {
        "CFBundleIdentifier": FORGE_BUNDLE_ID,
        "CFBundleVersion": "3",
        "CFBundleShortVersionString": "1.0.0",
        "ForgeArtifactVariant": "thin",
    }
    with zipfile.ZipFile(thin, "w") as archive:
        archive.writestr("Payload/Forge.app/Info.plist", plistlib.dumps(source_info))
        archive.writestr("Payload/Forge.app/Forge", b"executable")
        archive.writestr("Payload/Forge.app/_CodeSignature/CodeResources", b"stale")
        archive.writestr("Payload/Forge.app/embedded.mobileprovision", b"stale")
    assets, public = staged_seed_assets(tmp_path, keys)
    output = assemble_seed_ipa(
        thin, assets, tmp_path / "Forge-seed.ipa", sequence=1, public_key=public
    )
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        info = plistlib.loads(archive.read("Payload/Forge.app/Info.plist"))
        assert any("Payload/Forge.app/SeedAssets/chunks/" in name for name in names)
    assert info["CFBundleVersion"] == "2"
    assert info["ForgeArtifactVariant"] == "seed"
    assert not any("_CodeSignature" in name or name.endswith("embedded.mobileprovision") for name in names)


def test_seed_assembler_rejects_symlink_assets(tmp_path: Path, keys):
    thin = tmp_path / "Forge-thin.ipa"
    with zipfile.ZipFile(thin, "w") as archive:
        archive.writestr(
            "Payload/Forge.app/Info.plist",
            plistlib.dumps({"CFBundleIdentifier": FORGE_BUNDLE_ID}),
        )
    assets, public = staged_seed_assets(tmp_path, keys)
    target = tmp_path / "target"
    target.write_bytes(b"data")
    (assets / "link").symlink_to(target)
    with pytest.raises(PackageError):
        assemble_seed_ipa(
            thin, assets, tmp_path / "Forge-seed.ipa", sequence=1, public_key=public
        )


def test_seed_assembler_rejects_tampered_manifest(tmp_path: Path, keys):
    thin = tmp_path / "Forge-thin.ipa"
    with zipfile.ZipFile(thin, "w") as archive:
        archive.writestr(
            "Payload/Forge.app/Info.plist",
            plistlib.dumps({"CFBundleIdentifier": FORGE_BUNDLE_ID}),
        )
    assets, public = staged_seed_assets(tmp_path, keys)
    manifest_path = assets / "manifests" / "seed-test.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = "tampered"
    write_json(manifest_path, manifest)

    with pytest.raises(PackageError, match="invalid Seed asset manifest"):
        assemble_seed_ipa(
            thin, assets, tmp_path / "Forge-seed.ipa", sequence=1, public_key=public
        )
