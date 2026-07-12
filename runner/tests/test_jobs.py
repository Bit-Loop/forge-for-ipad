from __future__ import annotations

import time
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from .conftest import await_job


def job_request(
    snapshot: str, *, key: str = "build-1", argv: list[str] | None = None
) -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "snapshot_digest": snapshot,
        "argv": argv or ["fake", "success"],
        "artifact_globs": ["dist/**/*", "dist/*"],
    }


def test_job_lifecycle_idempotency_events_and_artifacts(
    client: TestClient, auth: dict[str, str], empty_snapshot: str
) -> None:
    request = job_request(empty_snapshot, argv=["fake", "write", "dist/result.txt", "compiled"])
    created = client.post("/forge/v1/jobs", json=request, headers=auth)
    assert created.status_code == 202
    assert created.json()["replayed"] is False
    job_id = created.json()["job"]["id"]
    completed = await_job(client, auth, job_id)
    assert completed["status"] == "succeeded"
    assert completed["exit_code"] == 0

    replay = client.post("/forge/v1/jobs", json=request, headers=auth)
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True
    assert replay.json()["job"]["id"] == job_id

    conflicting = job_request(empty_snapshot, argv=["fake", "fail"])
    assert client.post("/forge/v1/jobs", json=conflicting, headers=auth).status_code == 409

    events = client.get(f"/forge/v1/jobs/{job_id}/events?after=0&follow=false", headers=auth)
    assert events.status_code == 200
    assert "event: status" in events.text
    assert "event: output" in events.text
    assert "event: artifact" in events.text
    sequence_ids = [
        int(line.removeprefix("id: "))
        for line in events.text.splitlines()
        if line.startswith("id: ")
    ]
    assert sequence_ids == sorted(set(sequence_ids))

    after = sequence_ids[-2]
    replayed_events = client.get(
        f"/forge/v1/jobs/{job_id}/events?after={after}&follow=false", headers=auth
    )
    assert f"id: {after}" not in replayed_events.text
    assert f"id: {sequence_ids[-1]}" in replayed_events.text

    artifacts = client.get(f"/forge/v1/jobs/{job_id}/artifacts", headers=auth)
    assert artifacts.status_code == 200
    assert len(artifacts.json()) == 1
    artifact = artifacts.json()[0]
    assert artifact["name"] == "dist/result.txt"
    downloaded = client.get(f"/forge/v1/artifacts/{artifact['digest']}", headers=auth)
    assert downloaded.status_code == 200
    assert downloaded.content == b"compiled"


def test_failed_and_cancelled_jobs_are_durable(
    client: TestClient, auth: dict[str, str], empty_snapshot: str
) -> None:
    failure = client.post(
        "/forge/v1/jobs",
        json=job_request(empty_snapshot, key="fail", argv=["fake", "fail"]),
        headers=auth,
    )
    failed = await_job(client, auth, failure.json()["job"]["id"])
    assert failed["status"] == "failed"
    assert failed["exit_code"] == 2

    sleeping = client.post(
        "/forge/v1/jobs",
        json=job_request(empty_snapshot, key="sleep", argv=["fake", "sleep", "5"]),
        headers=auth,
    )
    job_id = sleeping.json()["job"]["id"]
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if client.get(f"/forge/v1/jobs/{job_id}", headers=auth).json()["status"] == "running":
            break
        time.sleep(0.01)
    cancelled = client.delete(f"/forge/v1/jobs/{job_id}", headers=auth)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_job_validation_and_missing_resources(
    client: TestClient, auth: dict[str, str], empty_snapshot: str
) -> None:
    assert (
        client.post("/forge/v1/jobs", json=job_request("f" * 64), headers=auth).status_code == 404
    )
    traversal = job_request(empty_snapshot, key="traversal")
    traversal["cwd"] = "../outside"
    assert client.post("/forge/v1/jobs", json=traversal, headers=auth).status_code == 422
    both = job_request(empty_snapshot, key="both")
    both["shell"] = "true"
    assert client.post("/forge/v1/jobs", json=both, headers=auth).status_code == 422
    steps = job_request(empty_snapshot, key="steps")
    steps["argv"] = None
    steps["steps"] = [["fake", "success"], ["fake", "write", "dist/steps.txt", "done"]]
    created_steps = client.post("/forge/v1/jobs", json=steps, headers=auth)
    assert created_steps.status_code == 202
    assert await_job(client, auth, created_steps.json()["job"]["id"])["status"] == "succeeded"
    step_artifacts = client.get(
        f"/forge/v1/jobs/{created_steps.json()['job']['id']}/artifacts", headers=auth
    ).json()
    assert [artifact["name"] for artifact in step_artifacts] == ["dist/steps.txt"]
    invalid_boundary = dict(steps)
    invalid_boundary["idempotency_key"] = "bad-network-boundary"
    invalid_boundary["network"] = {"enabled": True, "networked_steps": 3}
    assert client.post("/forge/v1/jobs", json=invalid_boundary, headers=auth).status_code == 422
    disabled_boundary = dict(steps)
    disabled_boundary["idempotency_key"] = "disabled-network-boundary"
    disabled_boundary["network"] = {"enabled": False, "networked_steps": 1}
    assert client.post("/forge/v1/jobs", json=disabled_boundary, headers=auth).status_code == 422
    invalid_glob = job_request(empty_snapshot, key="bad-glob", argv=["fake", "success"])
    invalid_glob["artifact_globs"] = ["../secret"]
    created = client.post("/forge/v1/jobs", json=invalid_glob, headers=auth)
    assert await_job(client, auth, created.json()["job"]["id"])["status"] == "failed"
    image_injection = job_request(empty_snapshot, key="bad-image")
    image_injection["image"] = "--volume=/host:/escape"
    assert client.post("/forge/v1/jobs", json=image_injection, headers=auth).status_code == 422


def test_unknown_jobs_and_artifacts_return_not_found(
    client: TestClient, auth: dict[str, str]
) -> None:
    assert client.get("/forge/v1/jobs/missing", headers=auth).status_code == 404
    assert client.delete("/forge/v1/jobs/missing", headers=auth).status_code == 404
    assert client.get("/forge/v1/jobs/missing/artifacts", headers=auth).status_code == 404
    assert client.get(f"/forge/v1/artifacts/{'0' * 64}", headers=auth).status_code == 404


def test_jobs_events_and_artifacts_are_token_owned(
    client: TestClient, auth: dict[str, str], empty_snapshot: str
) -> None:
    created = client.post(
        "/forge/v1/jobs",
        json=job_request(
            empty_snapshot,
            key="owner-only",
            argv=["fake", "write", "dist/private.txt", "private"],
        ),
        headers=auth,
    )
    job_id = created.json()["job"]["id"]
    await_job(client, auth, job_id)
    artifact = client.get(f"/forge/v1/jobs/{job_id}/artifacts", headers=auth).json()[0]

    cast(FastAPI, client.app).state.pairing.issue_code("654321")
    paired = client.post("/forge/v1/pair", json={"code": "654321", "client_name": "other-client"})
    other = {"Authorization": f"Bearer {paired.json()['token']}"}
    assert client.get(f"/forge/v1/jobs/{job_id}", headers=other).status_code == 404
    assert client.delete(f"/forge/v1/jobs/{job_id}", headers=other).status_code == 404
    assert client.get(f"/forge/v1/jobs/{job_id}/events", headers=other).status_code == 404
    assert client.get(f"/forge/v1/jobs/{job_id}/artifacts", headers=other).status_code == 404
    assert client.get(f"/forge/v1/artifacts/{artifact['digest']}", headers=other).status_code == 404


def test_runner_enforces_concurrency_limit(
    client: TestClient, auth: dict[str, str], empty_snapshot: str
) -> None:
    job_ids = [
        client.post(
            "/forge/v1/jobs",
            json=job_request(
                empty_snapshot,
                key=f"concurrency-{index}",
                argv=["fake", "sleep", "0.25"],
            ),
            headers=auth,
        ).json()["job"]["id"]
        for index in range(3)
    ]
    deadline = time.monotonic() + 1
    statuses: list[str] = []
    while time.monotonic() < deadline:
        statuses = [
            client.get(f"/forge/v1/jobs/{job_id}", headers=auth).json()["status"]
            for job_id in job_ids
        ]
        if statuses.count("running") == 2 and statuses.count("queued") == 1:
            break
        time.sleep(0.01)
    assert statuses.count("running") == 2
    assert statuses.count("queued") == 1
    assert all(await_job(client, auth, job_id)["status"] == "succeeded" for job_id in job_ids)
