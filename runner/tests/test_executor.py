from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge_runner.app import create_app
from forge_runner.config import Settings
from forge_runner.executor import (
    Emit,
    ExecutionResult,
    ExecutorUnavailable,
    FakeExecutor,
    PodmanExecutor,
)
from forge_runner.models import JobRequest

from .conftest import await_job


async def discard_event(_event_type: str, _data: dict[str, object]) -> None:
    pass


def request_for(snapshot: str, architecture: str, *, steps: list[list[str]]) -> JobRequest:
    return JobRequest(
        idempotency_key="executor-test",
        snapshot_digest=snapshot,
        steps=steps,
        target_architecture=architecture,
    )


def test_target_architecture_aliases_are_canonical() -> None:
    arm = request_for("0" * 64, "aarch64", steps=[["true"]])
    intel = request_for("0" * 64, "x86_64", steps=[["true"]])
    assert arm.target_architecture == "arm64"
    assert intel.target_architecture == "amd64"


class OwnerRecordingExecutor(FakeExecutor):
    target_architectures: tuple[str, ...] = ("arm64", "amd64")

    def __init__(self) -> None:
        super().__init__()
        self.owners: list[str] = []

    async def run(
        self,
        owner_token_id: str,
        job_id: str,
        request: JobRequest,
        workspace: Path,
        emit: Emit,
    ) -> ExecutionResult:
        self.owners.append(owner_token_id)
        return await super().run(owner_token_id, job_id, request, workspace, emit)


def test_job_manager_passes_database_owner_to_executor(tmp_path: Path) -> None:
    executor = OwnerRecordingExecutor()
    app = create_app(Settings(data_dir=tmp_path, pairing_code="123456"), executor=executor)
    with TestClient(app) as client:
        paired = client.post("/forge/v1/pair", json={"code": "123456", "client_name": "owner-test"})
        auth = {"Authorization": f"Bearer {paired.json()['token']}"}
        capabilities = client.get("/forge/v1/capabilities", headers=auth).json()
        assert capabilities["host_architecture"] in {"arm64", "amd64"}
        assert capabilities["target_architectures"] == list(executor.target_architectures)
        snapshot = client.post("/forge/v1/snapshots", json={"entries": []}, headers=auth).json()[
            "digest"
        ]
        created = client.post(
            "/forge/v1/jobs",
            json={
                "idempotency_key": "owner-propagation",
                "snapshot_digest": snapshot,
                "argv": ["fake", "success"],
            },
            headers=auth,
        )
        assert await_job(client, auth, created.json()["job"]["id"])["status"] == "succeeded"
        assert executor.owners == [paired.json()["token_id"]]


@pytest.mark.asyncio
async def test_podman_rejects_non_native_target_before_execution(tmp_path: Path) -> None:
    executor = PodmanExecutor(default_image="localhost/forge-workstation:test")
    other = "amd64" if executor.host_architecture == "arm64" else "arm64"
    with pytest.raises(ExecutorUnavailable, match="does not match Podman host"):
        await executor.run(
            "owner-token",
            "job-id",
            request_for("0" * 64, other, steps=[["true"]]),
            tmp_path,
            discard_event,
        )


class StubProcess:
    def __init__(self, *, container: bool = False, returncode: int = 0) -> None:
        self.returncode: int | None = None if container else returncode
        self.stdout = self._empty_stream() if container else None
        self.stderr = self._empty_stream() if container else None

    @staticmethod
    def _empty_stream() -> asyncio.StreamReader:
        stream = asyncio.StreamReader()
        stream.feed_eof()
        return stream

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.asyncio
async def test_podman_cache_is_bounded_partitioned_shared_and_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    async def create_process(*arguments: str, **_kwargs: Any) -> StubProcess:
        calls.append(arguments)
        return StubProcess(container=len(arguments) > 1 and arguments[1] == "run")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    cache_limit = 96 * 1024**2
    executor = PodmanExecutor(
        default_image="localhost/forge-workstation:test", max_cache_bytes=cache_limit
    )
    result = await executor.run(
        "private-owner-token",
        "public-job-id",
        request_for("0" * 64, executor.host_architecture, steps=[["first"], ["second"]]),
        tmp_path,
        discard_event,
    )

    assert result.exit_code == 0
    creates = [call for call in calls if call[1:3] == ("volume", "create")]
    removes = [call for call in calls if call[1:3] == ("volume", "rm")]
    runs = [call for call in calls if call[1] == "run"]
    assert len(creates) == 1
    assert len(removes) == 2  # preflight stale cleanup and unconditional final cleanup
    assert len(runs) == 2

    volume = creates[0][-1]
    assert volume.startswith("forge-job-cache-")
    assert "private-owner-token" not in volume
    assert "public-job-id" not in volume
    assert volume != executor._cache_volume_name("another-owner", "public-job-id")
    assert creates[0][-2] == (f"--opt=o=size={cache_limit},mode=0700,nosuid,nodev")
    assert all(f"--volume={volume}:/forge-cache:rw,U" in call for call in runs)
    assert all(call[-1] == volume for call in removes)
    assert calls.index(removes[0]) < calls.index(creates[0]) < calls.index(runs[0])
    assert calls[-1] == removes[-1]


@pytest.mark.asyncio
async def test_podman_reports_cache_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remove_count = 0
    events: list[str] = []

    async def create_process(*arguments: str, **_kwargs: Any) -> StubProcess:
        nonlocal remove_count
        if arguments[1:3] == ("volume", "rm"):
            remove_count += 1
            return StubProcess(returncode=0 if remove_count == 1 else 1)
        return StubProcess(container=arguments[1] == "run")

    async def record_event(event_type: str, _data: dict[str, object]) -> None:
        events.append(event_type)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    executor = PodmanExecutor(default_image="localhost/forge-workstation:test")
    with pytest.raises(ExecutorUnavailable, match="could not remove bounded job cache"):
        await executor.run(
            "owner-token",
            "job-id",
            request_for("0" * 64, executor.host_architecture, steps=[["true"]]),
            tmp_path,
            record_event,
        )
    assert "executor_cleanup_failed" in events


def test_podman_rejects_non_positive_cache_limit() -> None:
    with pytest.raises(ValueError, match="max_cache_bytes must be positive"):
        PodmanExecutor(max_cache_bytes=0)
