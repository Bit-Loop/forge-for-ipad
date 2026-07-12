from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    host: str = "0.0.0.0"  # noqa: S104 - the runner is intentionally a LAN service
    port: int = 4778
    pairing_code: str | None = None
    token_pepper: str = ""
    max_blob_bytes: int = 8 * 1024**3
    max_cas_bytes: int = 128 * 1024**3
    max_artifact_bytes: int = 4 * 1024**3
    max_job_artifacts_bytes: int = 8 * 1024**3
    max_job_artifact_count: int = 10_000
    max_artifact_storage_bytes: int = 64 * 1024**3
    max_artifact_count: int = 1_000_000
    max_artifact_metadata_bytes: int = 512 * 1024**2
    max_job_log_bytes: int = 64 * 1024**2
    max_event_storage_bytes: int = 2 * 1024**3
    max_event_count: int = 5_000_000
    max_database_bytes: int = 4 * 1024**3
    max_cache_bytes: int = 2 * 1024**3
    max_concurrent_jobs: int = 2
    default_image: str | None = None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "runner.sqlite3"

    @property
    def cas_dir(self) -> Path:
        return self.data_dir / "cas"

    @property
    def instance_id_path(self) -> Path:
        return self.data_dir / "instance-id"

    @classmethod
    def from_env(cls, data_dir: Path | None = None) -> Settings:
        return cls(
            data_dir=data_dir
            or Path(os.getenv("FORGE_DATA_DIR", "~/.local/share/forge-runner")).expanduser(),
            host=os.getenv("FORGE_HOST", "0.0.0.0"),  # noqa: S104 - intentional LAN service
            port=int(os.getenv("FORGE_PORT", "4778")),
            pairing_code=os.getenv("FORGE_PAIRING_CODE"),
            token_pepper=os.getenv("FORGE_TOKEN_PEPPER", ""),
            max_blob_bytes=int(os.getenv("FORGE_MAX_BLOB_BYTES", str(8 * 1024**3))),
            max_cas_bytes=int(os.getenv("FORGE_MAX_CAS_BYTES", str(128 * 1024**3))),
            max_artifact_bytes=int(os.getenv("FORGE_MAX_ARTIFACT_BYTES", str(4 * 1024**3))),
            max_job_artifacts_bytes=int(
                os.getenv("FORGE_MAX_JOB_ARTIFACTS_BYTES", str(8 * 1024**3))
            ),
            max_job_artifact_count=int(os.getenv("FORGE_MAX_JOB_ARTIFACT_COUNT", "10000")),
            max_artifact_storage_bytes=int(
                os.getenv("FORGE_MAX_ARTIFACT_STORAGE_BYTES", str(64 * 1024**3))
            ),
            max_artifact_count=int(os.getenv("FORGE_MAX_ARTIFACT_COUNT", "1000000")),
            max_artifact_metadata_bytes=int(
                os.getenv("FORGE_MAX_ARTIFACT_METADATA_BYTES", str(512 * 1024**2))
            ),
            max_job_log_bytes=int(os.getenv("FORGE_MAX_JOB_LOG_BYTES", str(64 * 1024**2))),
            max_event_storage_bytes=int(
                os.getenv("FORGE_MAX_EVENT_STORAGE_BYTES", str(2 * 1024**3))
            ),
            max_event_count=int(os.getenv("FORGE_MAX_EVENT_COUNT", "5000000")),
            max_database_bytes=int(os.getenv("FORGE_MAX_DATABASE_BYTES", str(4 * 1024**3))),
            max_cache_bytes=int(os.getenv("FORGE_MAX_CACHE_BYTES", str(2 * 1024**3))),
            max_concurrent_jobs=max(1, int(os.getenv("FORGE_MAX_CONCURRENT_JOBS", "2"))),
            default_image=os.getenv("FORGE_DEFAULT_IMAGE"),
        )
