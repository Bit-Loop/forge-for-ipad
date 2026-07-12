"""Command-line interface for Forge release engineering."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .assets import build_asset_pack, materialize_pack, verify_asset_pack
from .canonical import read_json, write_json
from .crypto import generate_keypair, load_private_key, load_public_key
from .packaging import (
    assemble_seed_ipa,
    package_metadata,
    payload_assembly_metadata,
    validate_package_metadata,
    validate_pair,
    validate_payload_assembly,
)
from .recovery import build_recovery_manifest, verify_recovery_manifest
from .sidestore import generate_source
from .validation import scan_private_material, validate_notice, validate_sbom


def _json_object(value: str) -> dict[str, Any]:
    result = read_json(value)
    if not isinstance(result, dict):
        raise ValueError(f"expected JSON object: {value}")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge-release")
    commands = parser.add_subparsers(dest="command", required=True)

    keygen = commands.add_parser("keygen", help="generate an Ed25519 release key")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-metadata", required=True)
    keygen.add_argument("--swift-output")

    asset = commands.add_parser("build-asset", help="chunk and sign a runtime asset pack")
    asset.add_argument("--source", required=True)
    asset.add_argument("--chunk-store", required=True)
    asset.add_argument("--manifest", required=True)
    asset.add_argument("--private-key", required=True)
    asset.add_argument("--pack-id", required=True)
    asset.add_argument("--version", required=True)
    asset.add_argument("--minimum-runtime-abi", required=True)
    asset.add_argument("--licenses", required=True, help="JSON array file")
    asset.add_argument("--compatibility", required=True, help="JSON object file")
    asset.add_argument("--chunk-bytes", type=int, default=4 * 1024 * 1024)

    verify_asset = commands.add_parser("verify-asset")
    verify_asset.add_argument("--manifest", required=True)
    verify_asset.add_argument("--public-key", required=True)
    verify_asset.add_argument("--chunk-store", required=True)

    materialize = commands.add_parser("materialize")
    materialize.add_argument("--manifest", required=True)
    materialize.add_argument("--public-key", required=True)
    materialize.add_argument("--chunk-store", required=True)
    materialize.add_argument("--runtime-root", required=True)

    package = commands.add_parser("package-metadata")
    package.add_argument("--ipa", required=True)
    package.add_argument("--kind", choices=("seed", "thin"), required=True)
    package.add_argument("--sequence", type=int, required=True)
    package.add_argument("--marketing-version", required=True)
    package.add_argument("--release-date", required=True)
    package.add_argument("--download-url", required=True)
    package.add_argument("--minimum-os-version", default="27.0")
    package.add_argument("--output", required=True)

    assembly = commands.add_parser("assembly-metadata")
    assembly.add_argument("--payload-root", required=True)
    assembly.add_argument("--kind", choices=("seed", "thin"), required=True)
    assembly.add_argument("--sequence", type=int, required=True)
    assembly.add_argument("--marketing-version", required=True)
    assembly.add_argument("--output", required=True)

    verify_assembly = commands.add_parser("verify-assembly")
    verify_assembly.add_argument("--metadata", required=True)
    verify_assembly.add_argument("--payload-root", required=True)

    seed_ipa = commands.add_parser("assemble-seed", help="embed recovery assets into a resignable Seed IPA")
    seed_ipa.add_argument("--thin-ipa", required=True)
    seed_ipa.add_argument("--seed-assets", required=True)
    seed_ipa.add_argument("--output", required=True)
    seed_ipa.add_argument("--sequence", type=int, required=True)
    seed_ipa.add_argument("--public-key", required=True)

    pair = commands.add_parser("validate-pair")
    pair.add_argument("--seed", required=True)
    pair.add_argument("--thin", required=True)

    feed = commands.add_parser("sidestore-feed")
    feed.add_argument("--package", action="append", required=True)
    feed.add_argument("--source-name", default="Forge for iPad")
    feed.add_argument("--source-identifier", default="com.bitloop.forge.source")
    feed.add_argument("--subtitle", default="Native development workstation for iPad")
    feed.add_argument("--description", default="Forge compiles, tests, debugs, and runs real projects on iPad.")
    feed.add_argument("--icon-url", required=True)
    feed.add_argument("--website", required=True)
    feed.add_argument("--output", required=True)

    recovery = commands.add_parser("recovery-manifest")
    recovery.add_argument("--file", action="append", required=True, help="role=/absolute/path")
    recovery.add_argument("--sequence", type=int, required=True)
    recovery.add_argument("--marketing-version", required=True)
    recovery.add_argument("--private-key", required=True)
    recovery.add_argument("--output", required=True)

    verify_recovery = commands.add_parser("verify-recovery")
    verify_recovery.add_argument("--manifest", required=True)
    verify_recovery.add_argument("--public-key", required=True)
    verify_recovery.add_argument("--recovery-root", required=True)

    validate = commands.add_parser("validate-release")
    validate.add_argument("--package-metadata", required=True)
    validate.add_argument("--ipa", required=True)
    validate.add_argument("--sbom", required=True)
    validate.add_argument("--notice", required=True)
    validate.add_argument("--required-component", action="append", default=[])
    validate.add_argument("--output-root", required=True)
    validate.add_argument("--private-key")
    return parser


def _execute(args: argparse.Namespace) -> None:
    if args.command == "keygen":
        generate_keypair(args.private_key, args.public_metadata, args.swift_output)
    elif args.command == "build-asset":
        licenses = read_json(args.licenses)
        compatibility = read_json(args.compatibility)
        if not isinstance(licenses, list) or not isinstance(compatibility, dict):
            raise ValueError("licenses must be an array and compatibility must be an object")
        manifest = build_asset_pack(
            args.source,
            args.chunk_store,
            pack_id=args.pack_id,
            version=args.version,
            minimum_runtime_abi=args.minimum_runtime_abi,
            licenses=licenses,
            compatibility=compatibility,
            private_key=load_private_key(args.private_key),
            chunk_bytes=args.chunk_bytes,
        )
        write_json(args.manifest, manifest)
    elif args.command == "verify-asset":
        verify_asset_pack(read_json(args.manifest), load_public_key(args.public_key), args.chunk_store)
    elif args.command == "materialize":
        result = materialize_pack(
            read_json(args.manifest), load_public_key(args.public_key), args.chunk_store, args.runtime_root
        )
        print(result["status"])
    elif args.command == "package-metadata":
        write_json(
            args.output,
            package_metadata(
                args.ipa,
                kind=args.kind,
                sequence=args.sequence,
                marketing_version=args.marketing_version,
                release_date=args.release_date,
                download_url=args.download_url,
                minimum_os_version=args.minimum_os_version,
            ),
        )
    elif args.command == "assembly-metadata":
        write_json(
            args.output,
            payload_assembly_metadata(
                args.payload_root,
                kind=args.kind,
                sequence=args.sequence,
                marketing_version=args.marketing_version,
            ),
        )
    elif args.command == "verify-assembly":
        validate_payload_assembly(_json_object(args.metadata), args.payload_root)
    elif args.command == "assemble-seed":
        print(
            assemble_seed_ipa(
                args.thin_ipa,
                args.seed_assets,
                args.output,
                sequence=args.sequence,
                public_key=load_public_key(args.public_key),
            )
        )
    elif args.command == "validate-pair":
        validate_pair(_json_object(args.seed), _json_object(args.thin))
    elif args.command == "sidestore-feed":
        write_json(
            args.output,
            generate_source(
                [_json_object(path) for path in args.package],
                source_name=args.source_name,
                source_identifier=args.source_identifier,
                subtitle=args.subtitle,
                description=args.description,
                icon_url=args.icon_url,
                website=args.website,
            ),
        )
    elif args.command == "recovery-manifest":
        files = []
        for value in args.file:
            role, separator, path = value.partition("=")
            if not separator:
                raise ValueError(f"invalid recovery file: {value}")
            files.append((role, path))
        write_json(
            args.output,
            build_recovery_manifest(
                files,
                release_sequence=args.sequence,
                marketing_version=args.marketing_version,
                private_key=load_private_key(args.private_key),
            ),
        )
    elif args.command == "verify-recovery":
        verify_recovery_manifest(read_json(args.manifest), load_public_key(args.public_key), args.recovery_root)
    elif args.command == "validate-release":
        metadata = _json_object(args.package_metadata)
        validate_package_metadata(metadata, args.ipa)
        validate_sbom(args.sbom, args.required_component)
        validate_notice(args.notice, args.required_component)
        scan_private_material(args.output_root, args.private_key)
    else:  # pragma: no cover
        raise AssertionError(args.command)


def main(argv: list[str] | None = None) -> int:
    try:
        _execute(_parser().parse_args(argv))
    except (OSError, ValueError, TypeError) as error:
        print(f"forge-release: {error}", file=sys.stderr)
        return 2
    return 0
