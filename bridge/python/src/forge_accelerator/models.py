from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

JsonObject = dict[str, Any]


class ComputeUnits(StrEnum):
    CPU = "cpu"
    CPU_GPU = "cpu_gpu"
    CPU_ANE = "cpu_ane"
    ALL = "all"


class BufferAccess(StrEnum):
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"


@dataclass(frozen=True, slots=True)
class ScratchReference:
    relative_path: str
    sha256: str
    size: int
    media_type: str = "application/octet-stream"
    delete_after_read: bool = False

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        segments = self.relative_path.split("/")
        if path.is_absolute() or any(segment in {"", ".", ".."} for segment in segments):
            raise ValueError("scratch path must be a normalized relative POSIX path")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        if self.size < 0:
            raise ValueError("size must be nonnegative")

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        scratch_root: str | Path,
        *,
        delete_after_read: bool = False,
    ) -> ScratchReference:
        source = Path(path).resolve(strict=True)
        root = Path(scratch_root).resolve(strict=True)
        try:
            relative = source.relative_to(root)
        except ValueError as error:
            raise ValueError("scratch object is outside the shared scratch root") from error
        if not source.is_file():
            raise ValueError("scratch object must be a regular file")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return cls(
            relative.as_posix(),
            digest.hexdigest(),
            source.stat().st_size,
            mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            delete_after_read,
        )

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> ScratchReference:
        return cls(
            relative_path=str(value["relative_path"]),
            sha256=str(value["sha256"]),
            size=int(value["size"]),
            media_type=str(value.get("media_type", "application/octet-stream")),
            delete_after_read=bool(value.get("delete_after_read", False)),
        )

    def to_wire(self) -> JsonObject:
        value: JsonObject = {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size": self.size,
            "media_type": self.media_type,
            "delete_after_read": self.delete_after_read,
        }
        return value


@dataclass(frozen=True, slots=True)
class InlineTensor:
    dtype: str
    shape: tuple[int, ...]
    data_base64: str

    def to_wire(self) -> JsonObject:
        return {
            "storage": "inline",
            "dtype": self.dtype,
            "shape": list(self.shape),
            "data_base64": self.data_base64,
        }


@dataclass(frozen=True, slots=True)
class ScratchTensor:
    dtype: str
    shape: tuple[int, ...]
    object: ScratchReference
    byte_offset: int = 0
    byte_length: int | None = None

    def to_wire(self) -> JsonObject:
        value: JsonObject = {
            "storage": "scratch",
            "dtype": self.dtype,
            "shape": list(self.shape),
            "object": self.object.to_wire(),
            "byte_offset": self.byte_offset,
        }
        if self.byte_length is not None:
            value["byte_length"] = self.byte_length
        return value


Tensor = InlineTensor | ScratchTensor


@dataclass(frozen=True, slots=True)
class MetalBuffer:
    index: int
    access: BufferAccess
    tensor: Tensor

    def __post_init__(self) -> None:
        if not 0 <= self.index <= 30:
            raise ValueError("Metal buffer index must be between zero and thirty")

    def to_wire(self) -> JsonObject:
        return {
            "index": self.index,
            "access": self.access.value,
            "tensor": self.tensor.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class Limits:
    max_request_bytes: int
    max_inline_bytes: int
    max_scratch_object_bytes: int
    max_tensor_rank: int
    max_inputs: int
    max_outputs: int
    max_concurrent_jobs: int
    max_model_handles: int
    max_library_handles: int
    max_model_bytes: int
    max_metal_source_bytes: int
    max_buffer_bytes: int
    job_retention_seconds: int

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> Limits:
        return cls(**{field: int(value[field]) for field in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class Capabilities:
    protocol_version: str
    server_version: str
    boot_id: str
    compute_units: tuple[ComputeUnits, ...]
    scratch_root: str
    coreml_available: bool
    metal_available: bool
    limits: Limits
    raw: Mapping[str, Any]

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> Capabilities:
        return cls(
            protocol_version=str(value["protocol_version"]),
            server_version=str(value["server_version"]),
            boot_id=str(value["boot_id"]),
            compute_units=tuple(ComputeUnits(unit) for unit in value["compute_units"]),
            scratch_root=str(value["scratch"]["guest_root"]),
            coreml_available=bool(value["coreml"]["available"]),
            metal_available=bool(value["metal"]["available"]),
            limits=Limits.from_wire(value["limits"]),
            raw=value,
        )


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    operation: str
    state: str
    progress: float | None
    result: Mapping[str, Any] | None
    error: Mapping[str, Any] | None
    raw: Mapping[str, Any]

    @property
    def terminal(self) -> bool:
        return self.state in {"succeeded", "failed", "cancelled"}

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> Job:
        progress = value.get("progress")
        return cls(
            id=str(value["id"]),
            operation=str(value["operation"]),
            state=str(value["state"]),
            progress=float(progress) if progress is not None else None,
            result=value.get("result"),
            error=value.get("error"),
            raw=value,
        )


@dataclass(frozen=True, slots=True)
class JobEventPage:
    events: tuple[Mapping[str, Any], ...]
    next_after: int
    terminal: bool

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> JobEventPage:
        return cls(tuple(value["events"]), int(value["next_after"]), bool(value["terminal"]))
