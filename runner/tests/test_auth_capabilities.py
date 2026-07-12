from __future__ import annotations

from typing import cast
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_health_is_public_but_capabilities_require_auth(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.get("/forge/v1/capabilities")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_pairing_is_one_time_and_token_authenticates(client: TestClient) -> None:
    assert (
        client.post("/forge/v1/pair", json={"code": "000000", "client_name": "bad"}).status_code
        == 401
    )
    paired = client.post("/forge/v1/pair", json={"code": "123456", "client_name": "Neo"})
    assert paired.status_code == 200
    body = paired.json()
    assert len(body["token"]) >= 40
    headers = {"Authorization": f"Bearer {body['token']}"}
    capabilities = client.get("/forge/v1/capabilities", headers=headers)
    assert capabilities.status_code == 200
    assert capabilities.json()["api_version"] == "forge/v1"
    assert str(UUID(capabilities.json()["instance_id"])) == capabilities.json()["instance_id"]
    assert capabilities.json()["executor"] == "fake"
    assert (
        client.post("/forge/v1/pair", json={"code": "123456", "client_name": "Replay"}).status_code
        == 401
    )


def test_malformed_auth_headers_are_rejected(client: TestClient) -> None:
    for value in ("token", "Basic abc", "Bearer", "Bearer "):
        assert (
            client.get("/forge/v1/capabilities", headers={"Authorization": value}).status_code
            == 401
        )


def test_explicit_repair_rotates_bearer_without_changing_owner(client: TestClient) -> None:
    first = client.post("/forge/v1/pair", json={"code": "123456", "client_name": "iPad"}).json()
    old_headers = {"Authorization": f"Bearer {first['token']}"}
    assert client.get("/forge/v1/capabilities", headers=old_headers).status_code == 200

    cast(FastAPI, client.app).state.pairing.issue_code("654321")
    second = client.post(
        "/forge/v1/pair",
        json={
            "code": "654321",
            "client_name": "iPad migrated",
            "existing_token_id": first["token_id"],
        },
    )
    assert second.status_code == 200
    assert second.json()["token_id"] == first["token_id"]
    assert second.json()["token"] != first["token"]
    assert client.get("/forge/v1/capabilities", headers=old_headers).status_code == 401
    new_headers = {"Authorization": f"Bearer {second.json()['token']}"}
    assert client.get("/forge/v1/capabilities", headers=new_headers).status_code == 200


def test_pairing_rate_limit_blocks_online_guessing(client: TestClient) -> None:
    for attempt in range(5):
        response = client.post(
            "/forge/v1/pair",
            json={"code": f"{attempt:06d}", "client_name": "guess"},
        )
        assert response.status_code == 401
    limited = client.post("/forge/v1/pair", json={"code": "123456", "client_name": "guess"})
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
