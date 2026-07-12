from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from .conftest import upload


def test_blob_upload_head_deduplication_and_digest_check(
    client: TestClient, auth: dict[str, str]
) -> None:
    content = b"int main(void) { return 0; }\n"
    digest = upload(client, auth, content)
    head = client.head(f"/forge/v1/blobs/{digest}", headers=auth)
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(content))
    assert head.headers["etag"] == f'"{digest}"'
    assert client.put(f"/forge/v1/blobs/{digest}", content=content, headers=auth).status_code == 204

    wrong = "0" * 64
    assert client.put(f"/forge/v1/blobs/{wrong}", content=content, headers=auth).status_code == 422
    assert client.head(f"/forge/v1/blobs/{'f' * 64}", headers=auth).status_code == 404


def test_snapshot_is_canonical_and_idempotent(client: TestClient, auth: dict[str, str]) -> None:
    source = b"print('hello')\n"
    digest = upload(client, auth, source)
    manifest = {
        "entries": [
            {"path": "src", "kind": "directory", "mode": 493},
            {
                "path": "src/main.py",
                "kind": "file",
                "digest": digest,
                "size": len(source),
                "mode": 420,
            },
            {"path": "main.py", "kind": "symlink", "target": "src/main.py", "mode": 511},
        ]
    }
    first = client.post("/forge/v1/snapshots", json=manifest, headers=auth)
    second = client.post(
        "/forge/v1/snapshots", json={"entries": list(reversed(manifest["entries"]))}, headers=auth
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["total_bytes"] == len(source)


def test_snapshot_rejects_missing_blobs_and_traversal(
    client: TestClient, auth: dict[str, str]
) -> None:
    missing = client.post(
        "/forge/v1/snapshots",
        json={
            "entries": [
                {
                    "path": "main.c",
                    "kind": "file",
                    "digest": hashlib.sha256(b"absent").hexdigest(),
                    "size": 6,
                }
            ]
        },
        headers=auth,
    )
    assert missing.status_code == 409

    for entry in (
        {"path": "../escape", "kind": "directory"},
        {"path": "/absolute", "kind": "directory"},
        {"path": "link", "kind": "symlink", "target": "../escape"},
        {"path": "a", "kind": "file", "digest": "0" * 64, "size": 0},
    ):
        response = client.post("/forge/v1/snapshots", json={"entries": [entry]}, headers=auth)
        assert response.status_code in {409, 422}


def test_blob_size_limit_is_enforced(client: TestClient, auth: dict[str, str]) -> None:
    content = b"x" * (1024 * 1024 + 1)
    digest = hashlib.sha256(content).hexdigest()
    assert client.put(f"/forge/v1/blobs/{digest}", content=content, headers=auth).status_code == 413
