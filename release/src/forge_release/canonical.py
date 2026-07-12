"""Canonical JSON primitives used as the signing boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class CanonicalJSONError(ValueError):
    pass


def _validate(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        raise CanonicalJSONError(f"floating point value is forbidden at {path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJSONError(f"non-string key at {path}")
            _validate(item, f"{path}.{key}")
        return
    raise CanonicalJSONError(f"unsupported {type(value).__name__} at {path}")


def canonical_bytes(value: Any) -> bytes:
    _validate(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def read_json(path: Path | str) -> Any:
    with Path(path).open("rb") as source:
        value = json.load(source)
    _validate(value)
    return value


def write_json(path: Path | str, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as output:
            output.write(canonical_bytes(value))
            output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
