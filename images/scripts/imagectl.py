#!/usr/bin/env python3
"""Offline validation and source verification for Forge guest images."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any


IMAGES_ROOT = Path(__file__).resolve().parents[1]
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
HASH_LENGTHS = {"sha1": 40, "sha256": 64}


class ContractError(ValueError):
    pass


def load(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def source_map(root: Path = IMAGES_ROOT) -> dict[str, dict[str, Any]]:
    document = load(root / "sources.lock.toml")
    if document.get("schema") != 1:
        raise ContractError("sources.lock.toml must use schema 1")
    result: dict[str, dict[str, Any]] = {}
    for source in document.get("source", []):
        source_id = source.get("id")
        if not isinstance(source_id, str) or not ID_PATTERN.fullmatch(source_id):
            raise ContractError(f"invalid source id: {source_id!r}")
        if source_id in result:
            raise ContractError(f"duplicate source id: {source_id}")
        algorithm = source.get("checksum_algorithm")
        if algorithm in HASH_LENGTHS:
            checksum = source.get("checksum", "")
            if not re.fullmatch(rf"[0-9a-f]{{{HASH_LENGTHS[algorithm]}}}", checksum):
                raise ContractError(f"invalid {algorithm} for source {source_id}")
            if algorithm == "sha1" and source.get("release_eligible"):
                raise ContractError(f"SHA-1 source cannot be release eligible: {source_id}")
        elif algorithm == "openpgp":
            fingerprint = source.get("signature_fingerprint", "")
            if not re.fullmatch(r"[0-9A-F]{40}", fingerprint):
                raise ContractError(f"invalid OpenPGP fingerprint for source {source_id}")
            if not source.get("signature_url"):
                raise ContractError(f"missing signature URL for source {source_id}")
        elif algorithm == "git-commit":
            if not re.fullmatch(r"[0-9a-f]{40}", source.get("checksum", "")):
                raise ContractError(f"invalid git commit for source {source_id}")
        else:
            raise ContractError(f"unsupported checksum algorithm for source {source_id}")
        payload_checksum = source.get("payload_checksum")
        if payload_checksum is not None and not re.fullmatch(r"[0-9a-f]{64}", payload_checksum):
            raise ContractError(f"invalid payload checksum for source {source_id}")
        result[source_id] = source
    return result


def pack_map(root: Path = IMAGES_ROOT) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "packs").glob("*.toml")):
        pack = load(path)
        pack_id = pack.get("id")
        if pack.get("schema") != 1 or not isinstance(pack_id, str) or not ID_PATTERN.fullmatch(pack_id):
            raise ContractError(f"invalid pack manifest: {path}")
        if pack_id in result:
            raise ContractError(f"duplicate pack id: {pack_id}")
        if path.stem != pack_id:
            raise ContractError(f"pack filename must match id: {path}")
        for manager in ("apt", "pacman"):
            packages = pack.get("packages", {}).get(manager, [])
            if len(packages) != len(set(packages)):
                raise ContractError(f"duplicate {manager} package in {pack_id}")
            if packages != sorted(packages):
                raise ContractError(f"{manager} packages must be sorted in {pack_id}")
        for section, key in (
            ("python_tools", "packages"),
            ("python_environment", "packages"),
            ("cargo_tools", "packages"),
            ("rustup", "components"),
            ("rustup", "targets"),
            ("services", "enable"),
        ):
            values = pack.get(section, {}).get(key, [])
            if len(values) != len(set(values)) or values != sorted(values):
                raise ContractError(f"{section}.{key} must be sorted and unique in {pack_id}")
        result[pack_id] = pack

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(pack_id: str) -> None:
        if pack_id in visited:
            return
        if pack_id in visiting:
            raise ContractError(f"pack dependency cycle at {pack_id}")
        if pack_id not in result:
            raise ContractError(f"unknown pack dependency: {pack_id}")
        visiting.add(pack_id)
        for dependency in result[pack_id].get("depends", []):
            visit(dependency)
        visiting.remove(pack_id)
        visited.add(pack_id)

    for pack_id in result:
        visit(pack_id)
    return result


def image_map(root: Path = IMAGES_ROOT) -> dict[str, dict[str, Any]]:
    sources = source_map(root)
    packs = pack_map(root)
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "manifests").glob("*.toml")):
        image = load(path)
        image_id = image.get("id")
        if image.get("schema") != 1 or not isinstance(image_id, str) or path.stem != image_id:
            raise ContractError(f"invalid image manifest: {path}")
        if image_id in result:
            raise ContractError(f"duplicate image id: {image_id}")
        if image.get("architecture") != "aarch64":
            raise ContractError(f"image must target aarch64: {image_id}")
        if image.get("source") not in sources:
            raise ContractError(f"unknown source in image {image_id}")
        unknown_packs = set(image.get("packs", [])) - packs.keys()
        if unknown_packs:
            raise ContractError(f"unknown packs in {image_id}: {sorted(unknown_packs)}")
        provisioner = root / image.get("provisioner", "")
        if not provisioner.is_file() or not provisioner.resolve().is_relative_to(root.resolve()):
            raise ContractError(f"invalid provisioner in {image_id}")
        result[image_id] = image
    return result


def validate(root: Path = IMAGES_ROOT) -> dict[str, int]:
    sources = source_map(root)
    packs = pack_map(root)
    images = image_map(root)
    for pack_id, pack in packs.items():
        for section in ("rustup", "uv"):
            source = pack.get(section, {}).get("source")
            if source is not None and source not in sources:
                raise ContractError(f"unknown {section} source in pack {pack_id}: {source}")
    policy = load(root / "policy/channels.toml")
    if policy.get("schema") != 1 or policy.get("default") != "stable":
        raise ContractError("channel policy must use schema 1 and default to stable")
    seeds = [image for image in images.values() if image.get("seed")]
    if len(seeds) != 1 or seeds[0]["id"] != "ubuntu-seed":
        raise ContractError("ubuntu-seed must be the only Seed image")
    for pack_id in seeds[0]["packs"]:
        if not packs[pack_id].get("seed"):
            raise ContractError(f"Seed image includes a post-install pack: {pack_id}")
    return {"sources": len(sources), "packs": len(packs), "images": len(images)}


def digest(path: Path, algorithm: str) -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_source(source_id: str, source_path: Path, signature_path: Path | None = None) -> dict[str, Any]:
    sources = source_map()
    try:
        source = sources[source_id]
    except KeyError as error:
        raise ContractError(f"unknown source: {source_id}") from error
    algorithm = source["checksum_algorithm"]
    expected_size = source.get("size")
    if expected_size is not None and source_path.stat().st_size != expected_size:
        raise ContractError(f"size mismatch for {source_path}")
    if algorithm in HASH_LENGTHS:
        actual = digest(source_path, algorithm)
        if actual != source["checksum"]:
            raise ContractError(f"{algorithm} mismatch for {source_path}")
    elif algorithm == "openpgp":
        if signature_path is None:
            raise ContractError("OpenPGP source verification requires --signature")
        completed = subprocess.run(
            ["gpgv", "--status-fd", "1", str(signature_path), str(source_path)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        expected = source["signature_fingerprint"]
        if completed.returncode or f"VALIDSIG {expected} " not in completed.stdout:
            raise ContractError("OpenPGP signature verification failed")
    else:
        raise ContractError(f"source {source['id']} is not a file payload")
    return {"source": source["id"], "verified": True, "release_eligible": source["release_eligible"]}


def verify(image_id: str, source_path: Path, signature_path: Path | None = None) -> dict[str, Any]:
    images = image_map()
    try:
        image = images[image_id]
    except KeyError as error:
        raise ContractError(f"unknown image: {image_id}") from error
    result = verify_source(image["source"], source_path, signature_path)
    return {"image": image_id, **result}


def plan(image_id: str) -> dict[str, Any]:
    images = image_map()
    try:
        image = images[image_id]
    except KeyError as error:
        raise ContractError(f"unknown image: {image_id}") from error
    source = source_map()[image["source"]]
    return {
        "image": image_id,
        "kind": image["kind"],
        "source": source["id"],
        "source_version": source["version"],
        "source_release_eligible": source["release_eligible"],
        "channel": image["forge_channel"],
        "packs": image["packs"],
        "provisioner": image["provisioner"],
    }


def release_check(image_id: str) -> dict[str, Any]:
    result = plan(image_id)
    if result["channel"] != "stable":
        raise ContractError(f"release image must use Forge stable: {image_id}")
    if not result["source_release_eligible"]:
        raise ContractError(f"source is not release eligible: {result['source']}")
    return {"image": image_id, "release_eligible": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="imagectl")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("image")
    release_parser = subparsers.add_parser("release-check")
    release_parser.add_argument("image")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("image")
    verify_parser.add_argument("source", type=Path)
    verify_parser.add_argument("--signature", type=Path)
    verify_source_parser = subparsers.add_parser("verify-source")
    verify_source_parser.add_argument("source_id")
    verify_source_parser.add_argument("source", type=Path)
    verify_source_parser.add_argument("--signature", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            result = validate()
        elif args.command == "plan":
            result = plan(args.image)
        elif args.command == "release-check":
            result = release_check(args.image)
        elif args.command == "verify":
            result = verify(args.image, args.source, args.signature)
        else:
            result = verify_source(args.source_id, args.source, args.signature)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ContractError, OSError, subprocess.SubprocessError) as error:
        print(f"imagectl: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
