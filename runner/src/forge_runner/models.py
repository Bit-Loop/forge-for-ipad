from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairRequest(StrictModel):
    code: Annotated[str, Field(min_length=6, max_length=128)]
    client_name: Annotated[str, Field(min_length=1, max_length=120)] = "Forge for iPad"
    existing_token_id: Annotated[str, Field(pattern=r"^[0-9a-f]{24}$")] | None = None


class PairResponse(StrictModel):
    token: str
    token_id: str


class CapabilityResponse(StrictModel):
    api_version: Literal["forge/v1"] = "forge/v1"
    instance_id: UUID
    server_version: str
    host_architecture: str
    target_architectures: list[str]
    executor: str
    features: list[str]


class SnapshotEntry(StrictModel):
    path: Annotated[str, Field(min_length=1, max_length=4096)]
    kind: Literal["file", "directory", "symlink"]
    digest: Digest | None = None
    size: Annotated[int, Field(ge=0)] | None = None
    mode: Annotated[int, Field(ge=0, le=0o7777)] = 0o644
    target: Annotated[str, Field(min_length=1, max_length=4096)] | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> SnapshotEntry:
        if self.kind == "file" and (self.digest is None or self.size is None):
            raise ValueError("file entries require digest and size")
        if self.kind == "symlink" and self.target is None:
            raise ValueError("symlink entries require target")
        if self.kind != "file" and (self.digest is not None or self.size is not None):
            raise ValueError("only file entries may specify digest or size")
        if self.kind != "symlink" and self.target is not None:
            raise ValueError("only symlink entries may specify target")
        return self


class SnapshotRequest(StrictModel):
    entries: Annotated[list[SnapshotEntry], Field(max_length=250_000)]


class SnapshotResponse(StrictModel):
    digest: Digest
    entry_count: int
    total_bytes: int


class ResourceLimits(StrictModel):
    cpus: Annotated[float, Field(gt=0, le=256)] = 2
    memory_mb: Annotated[int, Field(ge=64, le=1_048_576)] = 2048
    timeout_seconds: Annotated[int, Field(ge=1, le=7 * 24 * 3600)] = 3600
    pids: Annotated[int, Field(ge=16, le=1_048_576)] = 2048


class PublishedPort(StrictModel):
    container_port: Annotated[int, Field(ge=1, le=65535)]
    host_port: Annotated[int, Field(ge=1024, le=65535)] | None = None
    protocol: Literal["tcp", "udp"] = "tcp"


class NetworkPolicy(StrictModel):
    enabled: bool = False
    networked_steps: Annotated[int, Field(ge=1, le=64)] | None = None
    published_ports: Annotated[list[PublishedPort], Field(max_length=32)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def ports_require_network(self) -> NetworkPolicy:
        if (self.published_ports or self.networked_steps is not None) and not self.enabled:
            raise ValueError("network options require network access")
        return self


class JobRequest(StrictModel):
    idempotency_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
    snapshot_digest: Digest
    argv: Annotated[list[str], Field(min_length=1, max_length=1024)] | None = None
    steps: Annotated[list[list[str]], Field(min_length=1, max_length=64)] | None = None
    shell: Annotated[str, Field(min_length=1, max_length=131_072)] | None = None
    cwd: Annotated[str, Field(min_length=1, max_length=4096)] = "."
    image: (
        Annotated[
            str,
            Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"),
        ]
        | None
    ) = None
    target_architecture: Annotated[str, Field(pattern=r"^(arm64|aarch64|amd64|x86_64)$")] = "arm64"
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    secret_references: Annotated[list[str], Field(max_length=128)] = Field(default_factory=list)
    artifact_globs: Annotated[list[str], Field(max_length=128)] = Field(default_factory=list)

    @field_validator("target_architecture")
    @classmethod
    def normalize_target_architecture(cls, value: str) -> str:
        return {"aarch64": "arm64", "x86_64": "amd64"}.get(value, value)

    @model_validator(mode="after")
    def exactly_one_command(self) -> JobRequest:
        if sum(value is not None for value in (self.argv, self.shell, self.steps)) != 1:
            raise ValueError("exactly one of argv, shell, or steps is required")
        if self.argv and any(not value or "\x00" in value for value in self.argv):
            raise ValueError("argv entries must be non-empty and contain no NUL")
        if self.steps and any(
            not step or len(step) > 1024 or any(not value or "\x00" in value for value in step)
            for step in self.steps
        ):
            raise ValueError("steps must contain non-empty, NUL-free argv arrays")
        if self.network.networked_steps is not None:
            command_count = len(self.steps) if self.steps is not None else 1
            if self.network.networked_steps > command_count:
                raise ValueError("networked_steps exceeds the command count")
        if self.shell and "\x00" in self.shell:
            raise ValueError("shell command contains NUL")
        return self


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class JobResponse(StrictModel):
    id: str
    status: JobStatus
    request: JobRequest
    created_at: str
    updated_at: str
    exit_code: int | None = None
    error: str | None = None


class EventResponse(StrictModel):
    sequence: int
    type: str
    data: dict[str, object]
    created_at: str


class ArtifactResponse(StrictModel):
    name: str
    digest: Digest
    size: int
    media_type: str


class JobCreatedResponse(StrictModel):
    job: JobResponse
    replayed: bool


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
