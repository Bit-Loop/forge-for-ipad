from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from .client import AcceleratorClient, BridgeError
from .models import ComputeUnits, ScratchReference


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge-accelerator")
    parser.add_argument(
        "--endpoint",
        default="http://10.0.2.2:4777/accelerator/v1",
        help=argparse.SUPPRESS,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")
    job = commands.add_parser("job")
    job.add_argument("job_id")
    wait = commands.add_parser("wait")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=float)
    cancel = commands.add_parser("cancel")
    cancel.add_argument("job_id")
    reference = commands.add_parser("scratch-ref")
    reference.add_argument("path", type=Path)
    reference.add_argument("--root", type=Path, required=True)
    reference.add_argument("--delete-after-read", action="store_true")
    coreml = commands.add_parser("coreml-compile")
    coreml.add_argument("path", type=Path)
    coreml.add_argument("--root", type=Path, required=True)
    coreml.add_argument("--format", choices=("mlmodel",), required=True)
    coreml.add_argument("--compute-units", choices=tuple(ComputeUnits))
    metal = commands.add_parser("metal-compile")
    source = metal.add_mutually_exclusive_group(required=True)
    source.add_argument("--source")
    source.add_argument("--file", type=Path)
    metal.add_argument("--root", type=Path)
    metal.add_argument("--language-version")
    metal.add_argument("--no-fast-math", action="store_true")
    return parser


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "scratch-ref":
        _print(
            ScratchReference.from_file(
                arguments.path,
                arguments.root,
                delete_after_read=arguments.delete_after_read,
            ).to_wire()
        )
        return 0
    token = os.environ.get("FORGE_ACCEL_TOKEN")
    if not token:
        raise SystemExit("FORGE_ACCEL_TOKEN is required")
    client = AcceleratorClient(token, endpoint=arguments.endpoint)
    try:
        if arguments.command == "capabilities":
            _print(client.capabilities().raw)
        elif arguments.command == "job":
            _print(client.job(arguments.job_id).raw)
        elif arguments.command == "wait":
            _print(client.wait(arguments.job_id, timeout=arguments.timeout).raw)
        elif arguments.command == "cancel":
            _print(client.cancel(arguments.job_id).raw)
        elif arguments.command == "coreml-compile":
            reference = ScratchReference.from_file(arguments.path, arguments.root)
            units = ComputeUnits(arguments.compute_units) if arguments.compute_units else None
            _print(client.compile_coreml(reference, arguments.format, units).raw)
        elif arguments.command == "metal-compile":
            if arguments.source is not None:
                source = arguments.source
            elif arguments.root is not None:
                source = ScratchReference.from_file(arguments.file, arguments.root)
            else:
                source = arguments.file.read_text()
            _print(
                client.compile_metal(
                    source,
                    language_version=arguments.language_version,
                    fast_math=not arguments.no_fast_math,
                ).raw
            )
    except BridgeError as error:
        _print(
            {
                "error": {
                    "code": error.code,
                    "message": str(error),
                    "retriable": error.retriable,
                    "request_id": error.request_id,
                }
            }
        )
        return 1
    return 0
