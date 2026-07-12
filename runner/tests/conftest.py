from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge_runner.app import create_app
from forge_runner.config import Settings
from forge_runner.executor import FakeExecutor


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        Settings(
            data_dir=tmp_path,
            pairing_code="123456",
            token_pepper="test-only-pepper",
            max_blob_bytes=1024 * 1024,
            max_artifact_bytes=1024 * 1024,
            max_concurrent_jobs=2,
        ),
        executor=FakeExecutor(),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth(client: TestClient) -> dict[str, str]:
    response = client.post("/forge/v1/pair", json={"code": "123456", "client_name": "pytest"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def empty_snapshot(client: TestClient, auth: dict[str, str]) -> str:
    response = client.post("/forge/v1/snapshots", json={"entries": []}, headers=auth)
    assert response.status_code == 200
    return str(response.json()["digest"])


def upload(client: TestClient, auth: dict[str, str], content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    response = client.put(f"/forge/v1/blobs/{digest}", content=content, headers=auth)
    assert response.status_code == 201
    return digest


def await_job(
    client: TestClient, auth: dict[str, str], job_id: str, timeout: float = 2
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/forge/v1/jobs/{job_id}", headers=auth)
        assert response.status_code == 200
        job: dict[str, Any] = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish")
