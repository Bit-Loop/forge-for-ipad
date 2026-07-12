from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .conftest import await_job
from .test_jobs import job_request


def test_fake_executor_exposes_typed_pty_boundary(
    client: TestClient, auth: dict[str, str], empty_snapshot: str
) -> None:
    response = client.post(
        "/forge/v1/jobs", json=job_request(empty_snapshot, key="pty"), headers=auth
    )
    job_id = response.json()["job"]["id"]
    await_job(client, auth, job_id)
    with client.websocket_connect(f"/forge/v1/jobs/{job_id}/pty", headers=auth) as websocket:
        websocket.send_bytes(b"resize-and-input")
        assert websocket.receive_bytes() == b"resize-and-input"


def test_pty_rejects_missing_auth(client: TestClient) -> None:
    try:
        with client.websocket_connect("/forge/v1/jobs/anything/pty"):
            raise AssertionError("unauthenticated websocket was accepted")
    except WebSocketDisconnect as error:
        assert error.code == 4401
