from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from forge_runner.app import create_app
from forge_runner.config import Settings
from forge_runner.executor import FakeExecutor

from .conftest import await_job
from .test_jobs import job_request


def test_interrupted_job_returns_to_durable_queue_and_resumes(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, pairing_code="654321", token_pepper="recovery-test")
    first_app = create_app(settings, executor=FakeExecutor())
    with TestClient(first_app) as first:
        pair = first.post(
            "/forge/v1/pair", json={"code": "654321", "client_name": "persistent-client"}
        )
        assert pair.status_code == 200
        auth = {"Authorization": f"Bearer {pair.json()['token']}"}
        snapshot = first.post("/forge/v1/snapshots", json={"entries": []}, headers=auth).json()[
            "digest"
        ]
        submitted = first.post(
            "/forge/v1/jobs",
            json=job_request(snapshot, key="restart", argv=["fake", "sleep", "0.25"]),
            headers=auth,
        )
        job_id = submitted.json()["job"]["id"]
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if first.get(f"/forge/v1/jobs/{job_id}", headers=auth).json()["status"] == "running":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("job never started")

    second_app = create_app(settings, executor=FakeExecutor())
    with TestClient(second_app) as second:
        completed = await_job(second, auth, job_id)
        assert completed["status"] == "succeeded"
        events = second.get(f"/forge/v1/jobs/{job_id}/events?follow=false", headers=auth).text
        assert "event: suspended" in events
        assert events.count('"status":"running"') == 2
        # The configured one-time code does not become valid again after restart.
        assert (
            second.post(
                "/forge/v1/pair", json={"code": "654321", "client_name": "replay"}
            ).status_code
            == 401
        )
