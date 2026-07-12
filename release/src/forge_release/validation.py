"""SBOM, NOTICE, artifact digest, and private-key leak gates."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import BinaryIO, Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .assets import sha256_file
from .crypto import load_private_key


class ValidationError(ValueError):
    pass


PRIVATE_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"release-ed25519.key",
)


def validate_sbom(path: Path | str, required_packages: Iterable[str] = ()) -> set[str]:
    try:
        sbom = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError("SBOM is not valid JSON") from error
    if sbom.get("spdxVersion") != "SPDX-2.3" or sbom.get("dataLicense") != "CC0-1.0":
        raise ValidationError("SBOM must be SPDX 2.3 with CC0-1.0 data license")
    packages = sbom.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValidationError("SBOM contains no packages")
    names: set[str] = set()
    for package in packages:
        name = package.get("name")
        if not name or not package.get("licenseConcluded") or not package.get("licenseDeclared"):
            raise ValidationError("every SBOM package needs name and concluded/declared licenses")
        names.add(name)
    missing = set(required_packages) - names
    if missing:
        raise ValidationError(f"SBOM is missing required packages: {sorted(missing)}")
    return names


def validate_notice(path: Path | str, required_components: Iterable[str] = ()) -> None:
    try:
        notice = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValidationError("NOTICE must be readable UTF-8") from error
    if not notice.strip():
        raise ValidationError("NOTICE is empty")
    folded = notice.casefold()
    missing = [name for name in required_components if name.casefold() not in folded]
    if missing:
        raise ValidationError(f"NOTICE is missing components: {missing}")


def _private_tokens(private_key_path: Path | str | None) -> tuple[bytes, ...]:
    if private_key_path is None:
        return PRIVATE_MARKERS
    key: Ed25519PrivateKey = load_private_key(private_key_path)
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pem = Path(private_key_path).read_bytes().strip()
    return PRIVATE_MARKERS + (raw, pem)


def _stream_contains(source: BinaryIO, tokens: tuple[bytes, ...], block_size: int = 1024 * 1024) -> bool:
    overlap = max(map(len, tokens), default=1) - 1
    tail = b""
    while block := source.read(block_size):
        window = tail + block
        if any(token and token in window for token in tokens):
            return True
        tail = window[-overlap:] if overlap else b""
    return False


def scan_private_material(output_root: Path | str, private_key_path: Path | str | None = None) -> None:
    root, tokens = Path(output_root), _private_tokens(private_key_path)
    leaks: list[str] = []
    files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        if private_key_path is not None and path.resolve() == Path(private_key_path).resolve():
            leaks.append(str(path))
            continue
        with path.open("rb") as source:
            if _stream_contains(source, tokens):
                leaks.append(str(path))
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if not member.is_dir():
                        with archive.open(member) as source:
                            if _stream_contains(source, tokens):
                                leaks.append(f"{path}!{member.filename}")
    if leaks:
        raise ValidationError(f"private key material found in release output: {sorted(set(leaks))}")


def validate_digest(path: Path | str, expected_sha256: str, expected_size: int) -> None:
    artifact = Path(path)
    if artifact.stat().st_size != expected_size or sha256_file(artifact) != expected_sha256:
        raise ValidationError(f"artifact digest or size mismatch: {artifact}")
