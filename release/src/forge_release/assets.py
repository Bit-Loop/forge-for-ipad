"""Signed content-addressed runtime packs with crash-safe materialization."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .canonical import read_json, write_json
from .crypto import sign_document, verify_document

DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024
PACK_SCHEMA_VERSION = 1
IDENTITY_COMPONENT = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
MAX_IDENTITY_BYTES = 128


class AssetError(ValueError):
    pass


def _validate_identity_component(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > MAX_IDENTITY_BYTES
        or IDENTITY_COMPONENT.fullmatch(value) is None
    ):
        raise AssetError(f"invalid {field}")
    return value


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or any(part in ("", ".", "..") for part in candidate.parts):
        raise AssetError(f"unsafe asset path: {path!r}")
    return candidate


def _source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AssetError(f"symlinks are not supported in release packs: {path}")
        if path.is_file():
            yield path


def build_asset_pack(
    source_root: Path | str,
    chunk_store: Path | str,
    *,
    pack_id: str,
    version: str,
    minimum_runtime_abi: str,
    licenses: list[dict[str, str]],
    compatibility: dict[str, Any],
    private_key: Ed25519PrivateKey,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict[str, Any]:
    source = Path(source_root)
    store = Path(chunk_store)
    if not source.is_dir() or chunk_bytes <= 0:
        raise AssetError("source must be a directory and chunk size must be positive")
    _validate_identity_component(pack_id, "pack ID")
    _validate_identity_component(version, "pack version")
    store.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    chunks: dict[str, int] = {}
    expanded_size = 0
    for path in _source_files(source):
        relative = path.relative_to(source).as_posix()
        _safe_relative(relative)
        file_digest = hashlib.sha256()
        file_chunks: list[dict[str, Any]] = []
        size = 0
        with path.open("rb") as input_file:
            while block := input_file.read(chunk_bytes):
                digest = hashlib.sha256(block).hexdigest()
                target = store / digest
                if target.exists():
                    if target.stat().st_size != len(block) or sha256_file(target) != digest:
                        raise AssetError(f"corrupt existing chunk: {digest}")
                else:
                    temporary = store / f".{digest}.{os.getpid()}.tmp"
                    temporary.write_bytes(block)
                    os.replace(temporary, target)
                chunks[digest] = len(block)
                file_chunks.append({"sha256": digest, "size": len(block)})
                file_digest.update(block)
                size += len(block)
        files.append(
            {
                "chunks": file_chunks,
                "mode": path.stat().st_mode & 0o777,
                "path": relative,
                "sha256": file_digest.hexdigest(),
                "size": size,
            }
        )
        expanded_size += size
    unsigned = {
        "chunks": [{"sha256": digest, "size": chunks[digest]} for digest in sorted(chunks)],
        "compatibility": compatibility,
        "expanded_size": expanded_size,
        "files": files,
        "licenses": licenses,
        "minimum_runtime_abi": minimum_runtime_abi,
        "pack_id": pack_id,
        "schema_version": PACK_SCHEMA_VERSION,
        "version": version,
    }
    return sign_document(unsigned, private_key)


def verify_asset_pack(
    manifest: dict[str, Any], public_key: Ed25519PublicKey, chunk_store: Path | str
) -> dict[str, Any]:
    unsigned = verify_document(manifest, public_key)
    if unsigned.get("schema_version") != PACK_SCHEMA_VERSION:
        raise AssetError("unsupported asset manifest schema")
    _validate_identity_component(unsigned.get("pack_id"), "pack ID")
    _validate_identity_component(unsigned.get("version"), "pack version")
    store = Path(chunk_store)
    chunk_items = unsigned.get("chunks", [])
    declared = {item["sha256"]: item["size"] for item in chunk_items}
    if len(declared) != len(chunk_items):
        raise AssetError("duplicate chunks in manifest")
    referenced: set[str] = set()
    file_paths: set[str] = set()
    expanded_size = 0
    for item in unsigned.get("files", []):
        _safe_relative(item["path"])
        if item["path"] in file_paths:
            raise AssetError(f"duplicate file in manifest: {item['path']}")
        file_paths.add(item["path"])
        expected_size = sum(chunk["size"] for chunk in item["chunks"])
        if expected_size != item["size"] or not 0 <= item["mode"] <= 0o777:
            raise AssetError(f"invalid file metadata: {item['path']}")
        digest = hashlib.sha256()
        actual_size = 0
        for chunk in item["chunks"]:
            chunk_digest, chunk_size = chunk["sha256"], chunk["size"]
            if declared.get(chunk_digest) != chunk_size:
                raise AssetError(f"undeclared or inconsistent chunk: {chunk_digest}")
            path = store / chunk_digest
            if not path.is_file() or path.stat().st_size != chunk_size or sha256_file(path) != chunk_digest:
                raise AssetError(f"missing or corrupt chunk: {chunk_digest}")
            block = path.read_bytes()
            digest.update(block)
            actual_size += len(block)
            referenced.add(chunk_digest)
        if actual_size != item["size"] or digest.hexdigest() != item["sha256"]:
            raise AssetError(f"file digest mismatch: {item['path']}")
        expanded_size += actual_size
    if set(declared) != referenced or expanded_size != unsigned.get("expanded_size"):
        raise AssetError("manifest size or chunk inventory mismatch")
    return unsigned


def _verified_prefix(partial: Path, chunks: list[dict[str, Any]], store: Path) -> int:
    if not partial.exists():
        return 0
    verified = 0
    with partial.open("rb") as source:
        for chunk in chunks:
            block = source.read(chunk["size"])
            if len(block) != chunk["size"] or hashlib.sha256(block).hexdigest() != chunk["sha256"]:
                break
            verified += 1
        valid_bytes = sum(item["size"] for item in chunks[:verified])
    with partial.open("r+b") as output:
        output.truncate(valid_bytes)
    return verified


def _verify_tree(root: Path, unsigned: dict[str, Any], *, includes_stored_manifest: bool) -> bool:
    if not root.is_dir():
        return False
    for item in unsigned["files"]:
        path = root.joinpath(*_safe_relative(item["path"]).parts)
        if not path.is_file() or path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            return False
    expected = {item["path"] for item in unsigned["files"]}
    if includes_stored_manifest:
        expected.add(".forge-pack-manifest.json")
    actual = {path.relative_to(root).as_posix() for path in _source_files(root)}
    return actual == expected


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    return tuple((0, int(token)) if token.isdigit() else (1, token.lower()) for token in re.findall(r"\d+|[A-Za-z]+", version))


def materialize_pack(
    manifest: dict[str, Any],
    public_key: Ed25519PublicKey,
    chunk_store: Path | str,
    runtime_root: Path | str,
) -> dict[str, Any]:
    unsigned = verify_asset_pack(manifest, public_key, chunk_store)
    store, runtime = Path(chunk_store), Path(runtime_root)
    pack_id, version = unsigned["pack_id"], unsigned["version"]
    packs = runtime / "packs" / pack_id
    registry_path = runtime / "active-packs.json"
    registry = read_json(registry_path) if registry_path.exists() else {"schema_version": 1, "packs": {}}
    active_version = registry.get("packs", {}).get(pack_id)
    if active_version is not None:
        _validate_identity_component(active_version, "active pack version")
    if active_version and _version_key(active_version) > _version_key(version):
        active = packs / active_version
        stored_manifest = active / ".forge-pack-manifest.json"
        if stored_manifest.exists():
            current = verify_document(read_json(stored_manifest), public_key)
            if (
                current.get("pack_id") == pack_id
                and current.get("version") == active_version
                and _verify_tree(active, current, includes_stored_manifest=True)
            ):
                return {"pack_id": pack_id, "status": "kept_newer", "version": active_version}
    destination = packs / version
    if destination.is_dir() and _verify_tree(destination, unsigned, includes_stored_manifest=True):
        registry.setdefault("packs", {})[pack_id] = version
        write_json(registry_path, registry)
        return {"pack_id": pack_id, "status": "already_valid", "version": version}
    staging = packs / f".{version}.staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / ".forge-pack-manifest.json").unlink(missing_ok=True)
    for item in unsigned["files"]:
        target = staging.joinpath(*_safe_relative(item["path"]).parts)
        if target.is_file() and target.stat().st_size == item["size"] and sha256_file(target) == item["sha256"]:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.part")
        start = _verified_prefix(partial, item["chunks"], store)
        with partial.open("ab") as output:
            for chunk in item["chunks"][start:]:
                with (store / chunk["sha256"]).open("rb") as source:
                    shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
        if partial.stat().st_size != item["size"] or sha256_file(partial) != item["sha256"]:
            raise AssetError(f"materialized file did not verify: {item['path']}")
        os.chmod(partial, item["mode"])
        os.replace(partial, target)
    if not _verify_tree(staging, unsigned, includes_stored_manifest=False):
        raise AssetError("staging tree did not verify")
    write_json(staging / ".forge-pack-manifest.json", manifest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    os.replace(staging, destination)
    registry.setdefault("packs", {})[pack_id] = version
    write_json(registry_path, registry)
    return {"pack_id": pack_id, "status": "activated", "version": version}
