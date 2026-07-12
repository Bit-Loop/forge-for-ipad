from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Settings
from .database import Database
from .identity import load_or_create_instance_id
from .security import PairingService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Forge for iPad remote execution runner")
    result.add_argument("--data-dir", type=Path)
    result.add_argument("--host")
    result.add_argument("--port", type=int)
    result.add_argument("--log-level", default="info")
    return result


def main() -> None:
    arguments = parser().parse_args()
    settings = Settings.from_env(arguments.data_dir)
    if arguments.host is not None or arguments.port is not None:
        settings = Settings(
            data_dir=settings.data_dir,
            host=arguments.host or settings.host,
            port=arguments.port or settings.port,
            pairing_code=settings.pairing_code,
            token_pepper=settings.token_pepper,
            max_blob_bytes=settings.max_blob_bytes,
            max_cas_bytes=settings.max_cas_bytes,
            max_artifact_bytes=settings.max_artifact_bytes,
            max_job_artifacts_bytes=settings.max_job_artifacts_bytes,
            max_job_artifact_count=settings.max_job_artifact_count,
            max_artifact_storage_bytes=settings.max_artifact_storage_bytes,
            max_artifact_count=settings.max_artifact_count,
            max_artifact_metadata_bytes=settings.max_artifact_metadata_bytes,
            max_job_log_bytes=settings.max_job_log_bytes,
            max_event_storage_bytes=settings.max_event_storage_bytes,
            max_event_count=settings.max_event_count,
            max_database_bytes=settings.max_database_bytes,
            max_cache_bytes=settings.max_cache_bytes,
            max_concurrent_jobs=settings.max_concurrent_jobs,
            default_image=settings.default_image,
        )
    logging.basicConfig(level=arguments.log_level.upper())
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=arguments.log_level,
    )


def pair() -> None:
    arguments = parser().parse_args()
    settings = Settings.from_env(arguments.data_dir)
    database = Database(
        settings.database_path,
        token_pepper=settings.token_pepper,
        max_database_bytes=settings.max_database_bytes,
        max_event_bytes=settings.max_event_storage_bytes,
        max_event_count=settings.max_event_count,
        max_artifact_storage_bytes=settings.max_artifact_storage_bytes,
        max_artifact_count=settings.max_artifact_count,
        max_artifact_metadata_bytes=settings.max_artifact_metadata_bytes,
    )
    database.initialize()
    code = PairingService(database).issue_code(lifetime_minutes=15)
    print(code)


def identity() -> None:
    arguments = parser().parse_args()
    settings = Settings.from_env(arguments.data_dir)
    print(load_or_create_instance_id(settings.data_dir))
