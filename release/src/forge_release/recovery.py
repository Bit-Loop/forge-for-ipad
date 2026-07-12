"""Signed external-recovery inventories."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .assets import sha256_file
from .crypto import sign_document, verify_document


def build_recovery_manifest(
    files: Iterable[tuple[str, Path | str]],
    *,
    release_sequence: int,
    marketing_version: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    inventory = []
    for role, path_value in files:
        path = Path(path_value)
        if role not in {"seed_ipa", "thin_ipa", "signature", "sbom", "notice", "source", "public_key"}:
            raise ValueError(f"unsupported recovery role: {role}")
        if not path.is_file():
            raise FileNotFoundError(path)
        inventory.append({"filename": path.name, "role": role, "sha256": sha256_file(path), "size": path.stat().st_size})
    if not any(item["role"] == "seed_ipa" for item in inventory):
        raise ValueError("recovery manifest requires a Seed IPA")
    unsigned = {
        "files": sorted(inventory, key=lambda item: (item["role"], item["filename"])),
        "marketing_version": marketing_version,
        "release_sequence": release_sequence,
        "schema_version": 1,
    }
    return sign_document(unsigned, private_key)


def verify_recovery_manifest(
    manifest: dict[str, Any], public_key: Ed25519PublicKey, recovery_root: Path | str
) -> dict[str, Any]:
    unsigned = verify_document(manifest, public_key)
    if unsigned.get("schema_version") != 1:
        raise ValueError("unsupported recovery manifest schema")
    root = Path(recovery_root).resolve()
    for item in unsigned.get("files", []):
        path = (root / item["filename"]).resolve()
        if path.parent != root or not path.is_file():
            raise ValueError(f"missing or unsafe recovery file: {item['filename']}")
        if path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            raise ValueError(f"invalid recovery file: {item['filename']}")
    return unsigned
