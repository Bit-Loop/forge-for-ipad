"""Thin/Seed artifact metadata and immutable size/build-number policy."""

from __future__ import annotations

import plistlib
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Literal
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .assets import sha256_file, verify_asset_pack
from .canonical import read_json

THIN_BUDGET_BYTES = 500_000_000
SEED_BUDGET_BYTES = 3_800_000_000
FORGE_BUNDLE_ID = "com.bitloop.forge"
PACKAGE_SCHEMA_VERSION = 1
ASSEMBLY_SCHEMA_VERSION = 1
SEED_DIRECTORY = "SeedAssets"


class PackageError(ValueError):
    pass


def paired_build_numbers(sequence: int) -> dict[str, int]:
    if sequence < 1:
        raise PackageError("release sequence must be positive")
    return {"seed": 2 * sequence, "thin": 2 * sequence + 1}


def artifact_budget(kind: Literal["thin", "seed"]) -> int:
    if kind == "thin":
        return THIN_BUDGET_BYTES
    if kind == "seed":
        return SEED_BUDGET_BYTES
    raise PackageError(f"unsupported package kind: {kind}")


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PackageError(f"unsafe IPA member: {name}")
    return path


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _seed_info(source: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    candidates = [
        info.filename
        for info in source.infolist()
        if PurePosixPath(info.filename).match("Payload/*.app/Info.plist")
    ]
    if len(candidates) != 1:
        raise PackageError("thin IPA must contain exactly one application Info.plist")
    name = candidates[0]
    try:
        info = plistlib.loads(source.read(name))
    except (plistlib.InvalidFileException, ValueError) as error:
        raise PackageError("application Info.plist is invalid") from error
    if info.get("CFBundleIdentifier") != FORGE_BUNDLE_ID:
        raise PackageError(f"Forge package bundle ID must be {FORGE_BUNDLE_ID}")
    return name, info


def _write_member(destination: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes) -> None:
    clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    clone.compress_type = zipfile.ZIP_DEFLATED
    clone.comment = info.comment
    clone.create_system = info.create_system
    clone.external_attr = info.external_attr
    clone.extra = b""
    destination.writestr(clone, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def assemble_seed_ipa(
    thin_ipa: Path | str,
    seed_assets: Path | str,
    output_ipa: Path | str,
    *,
    sequence: int,
    public_key: Ed25519PublicKey,
) -> Path:
    """Create an unsigned/resignable Seed IPA from the tested thin application.

    Seed bytes live at ``Payload/Forge.app/SeedAssets`` so the application can
    access them through ``Bundle.main``. Existing code signatures are removed;
    SideStore or another sideloading signer must sign the final artifact.
    """
    source_path, asset_root, output_path = Path(thin_ipa), Path(seed_assets), Path(output_ipa)
    if not source_path.is_file():
        raise PackageError(f"thin IPA does not exist: {source_path}")
    if not asset_root.is_dir():
        raise PackageError(f"Seed asset root does not exist: {asset_root}")
    manifests_root, chunks_root = asset_root / "manifests", asset_root / "chunks"
    manifests = sorted(manifests_root.glob("*.json")) if manifests_root.is_dir() else []
    if not manifests or not chunks_root.is_dir():
        raise PackageError("Seed assets must contain manifests/*.json and chunks/")
    for chunk in chunks_root.iterdir():
        if chunk.is_file() and not re.fullmatch(r"[0-9a-f]{64}", chunk.name):
            raise PackageError(f"invalid Seed chunk filename: {chunk.name}")
    for manifest in manifests:
        try:
            verify_asset_pack(read_json(manifest), public_key, chunks_root)
        except (OSError, ValueError, TypeError) as error:
            raise PackageError(f"invalid Seed asset manifest {manifest.name}: {error}") from error
    assets = [path for path in sorted(asset_root.rglob("*")) if path.is_file()]
    if not assets:
        raise PackageError("Seed asset root is empty")
    for path in asset_root.rglob("*"):
        if path.is_symlink():
            raise PackageError(f"symlinks are forbidden in Seed assets: {path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".partial")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(source_path) as source:
            info_name, app_info = _seed_info(source)
            app_root = PurePosixPath(info_name).parent
            seed_prefix = app_root / SEED_DIRECTORY
            for member in source.infolist():
                path = _safe_member(member.filename)
                if _is_symlink(member):
                    raise PackageError(f"symlinks are forbidden in IPA input: {member.filename}")
                if seed_prefix == path or seed_prefix in path.parents:
                    raise PackageError("thin IPA already contains Seed assets")

            app_info["CFBundleVersion"] = str(paired_build_numbers(sequence)["seed"])
            app_info["ForgeArtifactVariant"] = "seed"
            with zipfile.ZipFile(temporary, "w", allowZip64=True) as destination:
                for member in source.infolist():
                    path = _safe_member(member.filename)
                    if "_CodeSignature" in path.parts or path.name == "embedded.mobileprovision":
                        continue
                    data = plistlib.dumps(app_info, fmt=plistlib.FMT_BINARY) if member.filename == info_name else source.read(member)
                    _write_member(destination, member, data)

                for path in assets:
                    relative = path.relative_to(asset_root)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise PackageError(f"unsafe Seed asset: {relative}")
                    name = (seed_prefix / PurePosixPath(relative.as_posix())).as_posix()
                    member = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    member.external_attr = 0o100644 << 16
                    _write_member(destination, member, path.read_bytes())
        if temporary.stat().st_size > SEED_BUDGET_BYTES:
            raise PackageError(
                f"seed IPA is {temporary.stat().st_size} bytes; exact budget is {SEED_BUDGET_BYTES} bytes"
            )
        temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return output_path


def package_metadata(
    ipa_path: Path | str,
    *,
    kind: Literal["thin", "seed"],
    sequence: int,
    marketing_version: str,
    release_date: str,
    download_url: str,
    minimum_os_version: str = "27.0",
    bundle_id: str = FORGE_BUNDLE_ID,
    asset_packs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    artifact = Path(ipa_path)
    if not artifact.is_file():
        raise PackageError(f"IPA does not exist: {artifact}")
    if bundle_id != FORGE_BUNDLE_ID:
        raise PackageError(f"Forge package bundle ID must be {FORGE_BUNDLE_ID}")
    size, budget = artifact.stat().st_size, artifact_budget(kind)
    if size > budget:
        raise PackageError(f"{kind} IPA is {size} bytes; exact budget is {budget} bytes")
    return {
        "artifact": {
            "filename": artifact.name,
            "sha256": sha256_file(artifact),
            "size": size,
        },
        "asset_packs": asset_packs or [],
        "budget_bytes": budget,
        "build_number": paired_build_numbers(sequence)[kind],
        "bundle_id": bundle_id,
        "download_url": download_url,
        "kind": kind,
        "marketing_version": marketing_version,
        "minimum_os_version": minimum_os_version,
        "release_date": release_date,
        "release_sequence": sequence,
        "schema_version": PACKAGE_SCHEMA_VERSION,
    }


def payload_assembly_metadata(
    payload_root: Path | str,
    *,
    kind: Literal["thin", "seed"],
    sequence: int,
    marketing_version: str,
) -> dict[str, Any]:
    """Inventory the exact staging tree that will be compressed into an IPA.

    `Payload/` holds the application. `Payload/*.app/SeedAssets/` is the only
    location permitted for offline runtime data and is forbidden in the thin artifact.
    Compliance material can live under `Notices/`.
    """
    root = Path(payload_root)
    if not root.is_dir() or not (root / "Payload").is_dir():
        raise PackageError("IPA staging root must contain Payload/")
    files: list[dict[str, Any]] = []
    has_application = False
    has_seed_asset = False
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PackageError(f"symlinks are forbidden in IPA assembly staging: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        first = relative.split("/", 1)[0]
        if first == "Payload":
            role = "seed_runtime" if SEED_DIRECTORY in PurePosixPath(relative).parts else "application"
            has_application = has_application or ".app/" in relative
        elif first == "Notices":
            role = "compliance"
        else:
            role = "metadata"
        has_seed_asset = has_seed_asset or role == "seed_runtime"
        files.append(
            {
                "path": relative,
                "role": role,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    if not has_application:
        raise PackageError("Payload/ does not contain a Forge .app bundle")
    if kind == "thin" and has_seed_asset:
        raise PackageError("thin IPA staging must not contain SeedAssets/")
    if kind == "seed" and not has_seed_asset:
        raise PackageError("Seed IPA staging must contain SeedAssets/")
    return {
        "artifact_budget_bytes": artifact_budget(kind),
        "build_number": paired_build_numbers(sequence)[kind],
        "bundle_id": FORGE_BUNDLE_ID,
        "expanded_payload_bytes": sum(item["size"] for item in files),
        "files": files,
        "kind": kind,
        "marketing_version": marketing_version,
        "release_sequence": sequence,
        "schema_version": ASSEMBLY_SCHEMA_VERSION,
    }


def validate_payload_assembly(metadata: dict[str, Any], payload_root: Path | str | None = None) -> None:
    if metadata.get("schema_version") != ASSEMBLY_SCHEMA_VERSION:
        raise PackageError("unsupported assembly metadata schema")
    kind, sequence = metadata.get("kind"), metadata.get("release_sequence")
    if metadata.get("bundle_id") != FORGE_BUNDLE_ID:
        raise PackageError("unexpected assembly bundle ID")
    if metadata.get("artifact_budget_bytes") != artifact_budget(kind):
        raise PackageError("assembly budget is not the exact policy value")
    if metadata.get("build_number") != paired_build_numbers(sequence)[kind]:
        raise PackageError("assembly build number violates paired release policy")
    roles = {item.get("role") for item in metadata.get("files", [])}
    if "application" not in roles or (kind == "thin" and "seed_runtime" in roles) or (kind == "seed" and "seed_runtime" not in roles):
        raise PackageError("assembly roles violate thin/Seed payload policy")
    if sum(item["size"] for item in metadata["files"]) != metadata.get("expanded_payload_bytes"):
        raise PackageError("assembly expanded size mismatch")
    if payload_root is not None:
        root = Path(payload_root).resolve()
        for item in metadata["files"]:
            path = (root / item["path"]).resolve()
            if root not in path.parents or not path.is_file():
                raise PackageError(f"missing or unsafe assembly file: {item['path']}")
            if path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
                raise PackageError(f"assembly file mismatch: {item['path']}")


def validate_package_metadata(metadata: dict[str, Any], ipa_path: Path | str | None = None) -> None:
    if metadata.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise PackageError("unsupported package metadata schema")
    kind, sequence = metadata.get("kind"), metadata.get("release_sequence")
    if metadata.get("bundle_id") != FORGE_BUNDLE_ID:
        raise PackageError("unexpected bundle ID")
    if metadata.get("budget_bytes") != artifact_budget(kind):
        raise PackageError("package budget is not the exact policy value")
    if metadata.get("build_number") != paired_build_numbers(sequence)[kind]:
        raise PackageError("package build number violates paired release policy")
    if metadata["artifact"]["size"] > metadata["budget_bytes"]:
        raise PackageError("package exceeds size budget")
    if ipa_path is not None:
        artifact = Path(ipa_path)
        if artifact.stat().st_size != metadata["artifact"]["size"] or sha256_file(artifact) != metadata["artifact"]["sha256"]:
            raise PackageError("IPA does not match package metadata")


def validate_pair(seed: dict[str, Any], thin: dict[str, Any]) -> None:
    validate_package_metadata(seed)
    validate_package_metadata(thin)
    if seed["kind"] != "seed" or thin["kind"] != "thin":
        raise PackageError("expected Seed then thin metadata")
    common = ("release_sequence", "marketing_version", "bundle_id", "minimum_os_version")
    if any(seed[field] != thin[field] for field in common):
        raise PackageError("Seed and thin artifacts do not describe one replaceable release")
    if thin["build_number"] != seed["build_number"] + 1:
        raise PackageError("thin build must immediately follow its Seed build")
