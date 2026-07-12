from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_runner.app import create_app
from forge_runner.cas import (
    BlobTooLarge,
    ContentAddressedStore,
    DigestMismatch,
    StorageQuotaExceeded,
)
from forge_runner.config import Settings
from forge_runner.database import Database
from forge_runner.executor import FakeExecutor

from .conftest import await_job
from .test_jobs import job_request


async def chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        await asyncio.sleep(0)
        yield value


@pytest.mark.asyncio
async def test_cas_enforces_deduplicated_upload_and_durable_aggregate_quota(
    tmp_path: Path,
) -> None:
    store = ContentAddressedStore(tmp_path / "cas", max_blob_bytes=4, max_total_bytes=5)
    store.initialize()
    first = b"abc"
    first_digest = hashlib.sha256(first).hexdigest()
    assert await store.put_stream(first_digest, chunks(first)) == (True, 3)

    with pytest.raises(BlobTooLarge):
        await store.put_stream(first_digest, chunks(b"abcde"))
    with pytest.raises(DigestMismatch):
        await store.put_stream(first_digest, chunks(b"abd"))

    second = b"def"
    with pytest.raises(StorageQuotaExceeded):
        await store.put_stream(hashlib.sha256(second).hexdigest(), chunks(second))
    assert store.used_bytes == 3

    restarted = ContentAddressedStore(tmp_path / "cas", max_blob_bytes=4, max_total_bytes=5)
    restarted.initialize()
    assert restarted.used_bytes == 3
    with pytest.raises(StorageQuotaExceeded):
        await restarted.put_stream(hashlib.sha256(second).hexdigest(), chunks(second))


@pytest.mark.asyncio
async def test_concurrent_cas_reservations_cannot_overcommit(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "cas", max_blob_bytes=4, max_total_bytes=5)
    store.initialize()

    async def upload(content: bytes) -> bool:
        try:
            await store.put_stream(hashlib.sha256(content).hexdigest(), chunks(content))
        except StorageQuotaExceeded:
            return False
        return True

    results = await asyncio.gather(upload(b"abc"), upload(b"def"))
    assert sorted(results) == [False, True]
    assert store.used_bytes == 3


def quota_client(
    tmp_path: Path,
    *,
    max_job_artifact_count: int = 10,
    max_artifact_count: int = 10,
    max_artifact_metadata_bytes: int = 1024 * 1024,
    max_event_storage_bytes: int = 1024 * 1024,
    max_event_count: int = 100,
    max_database_bytes: int = 8 * 1024 * 1024,
) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                data_dir=tmp_path,
                pairing_code="123456",
                token_pepper="quota-test",
                max_blob_bytes=1024 * 1024,
                max_cas_bytes=4 * 1024 * 1024,
                max_artifact_bytes=1024 * 1024,
                max_job_artifacts_bytes=2 * 1024 * 1024,
                max_job_artifact_count=max_job_artifact_count,
                max_artifact_storage_bytes=2 * 1024 * 1024,
                max_artifact_count=max_artifact_count,
                max_artifact_metadata_bytes=max_artifact_metadata_bytes,
                max_job_log_bytes=1024 * 1024,
                max_event_storage_bytes=max_event_storage_bytes,
                max_event_count=max_event_count,
                max_database_bytes=max_database_bytes,
            ),
            executor=FakeExecutor(),
        )
    )


def pair_and_snapshot(client: TestClient) -> tuple[dict[str, str], str]:
    paired = client.post("/forge/v1/pair", json={"code": "123456", "client_name": "quota"})
    auth = {"Authorization": f"Bearer {paired.json()['token']}"}
    snapshot = client.post("/forge/v1/snapshots", json={"entries": []}, headers=auth)
    return auth, str(snapshot.json()["digest"])


def app_database(client: TestClient) -> Database:
    app = cast(FastAPI, client.app)
    return cast(Database, app.state.database)


def test_event_count_quota_preserves_durable_job_state(tmp_path: Path) -> None:
    with quota_client(tmp_path, max_event_count=2) as client:
        auth, snapshot = pair_and_snapshot(client)
        created = client.post(
            "/forge/v1/jobs", json=job_request(snapshot, key="event-limit"), headers=auth
        )
        completed = await_job(client, auth, created.json()["job"]["id"])
        assert completed["status"] == "succeeded"
        count, _, event_bytes = app_database(client).storage_usage("events")
        assert count == 2
        assert event_bytes <= 1024 * 1024


def test_event_byte_quota_preserves_durable_job_state(tmp_path: Path) -> None:
    with quota_client(tmp_path, max_event_storage_bytes=1) as client:
        auth, snapshot = pair_and_snapshot(client)
        created = client.post(
            "/forge/v1/jobs", json=job_request(snapshot, key="event-byte-limit"), headers=auth
        )
        completed = await_job(client, auth, created.json()["job"]["id"])
        assert completed["status"] == "succeeded"
        assert app_database(client).storage_usage("events") == (0, 0, 0)


def test_artifact_count_and_metadata_quotas_fail_job_without_overgrowth(tmp_path: Path) -> None:
    with quota_client(
        tmp_path,
        max_job_artifact_count=10,
        max_artifact_count=1,
        max_artifact_metadata_bytes=512,
    ) as client:
        auth, snapshot = pair_and_snapshot(client)
        request = job_request(snapshot, key="artifact-limit")
        request["argv"] = None
        request["steps"] = [
            ["fake", "write", "dist/one.txt", "one"],
            ["fake", "write", "dist/two.txt", "two"],
        ]
        created = client.post("/forge/v1/jobs", json=request, headers=auth)
        completed = await_job(client, auth, created.json()["job"]["id"])
        assert completed["status"] == "failed"
        count, content_bytes, metadata_bytes = app_database(client).storage_usage("artifacts")
        assert count <= 1
        assert content_bytes <= 2 * 1024 * 1024
        assert metadata_bytes <= 512


def test_artifact_metadata_quota_is_enforced_independently(tmp_path: Path) -> None:
    with quota_client(
        tmp_path,
        max_job_artifact_count=10,
        max_artifact_count=10,
        max_artifact_metadata_bytes=1,
    ) as client:
        auth, snapshot = pair_and_snapshot(client)
        created = client.post(
            "/forge/v1/jobs",
            json=job_request(
                snapshot,
                key="artifact-metadata-limit",
                argv=["fake", "write", "dist/result.txt", "result"],
            ),
            headers=auth,
        )
        completed = await_job(client, auth, created.json()["job"]["id"])
        assert completed["status"] == "failed"
        assert app_database(client).storage_usage("artifacts") == (0, 0, 0)


def test_artifact_aggregate_quota_survives_runner_restart(tmp_path: Path) -> None:
    with quota_client(tmp_path, max_artifact_count=1) as first:
        auth, snapshot = pair_and_snapshot(first)
        created = first.post(
            "/forge/v1/jobs",
            json=job_request(
                snapshot,
                key="before-restart",
                argv=["fake", "write", "dist/first.txt", "first"],
            ),
            headers=auth,
        )
        assert await_job(first, auth, created.json()["job"]["id"])["status"] == "succeeded"

    with quota_client(tmp_path, max_artifact_count=1) as second:
        created = second.post(
            "/forge/v1/jobs",
            json=job_request(
                snapshot,
                key="after-restart",
                argv=["fake", "write", "dist/second.txt", "second"],
            ),
            headers=auth,
        )
        assert await_job(second, auth, created.json()["job"]["id"])["status"] == "failed"
        count, _, _ = app_database(second).storage_usage("artifacts")
        assert count == 1


def test_sqlite_page_limit_is_configured_from_durable_database_quota(tmp_path: Path) -> None:
    limit = 8 * 1024 * 1024
    with quota_client(tmp_path, max_database_bytes=limit) as client:
        database = app_database(client)
        with database.connect() as connection:
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            max_pages = int(connection.execute("PRAGMA max_page_count").fetchone()[0])
        assert max_pages * page_size <= limit
