from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath

from .cas import BlobTooLarge, ContentAddressedStore, StorageQuotaExceeded
from .database import Database, IdempotencyConflict
from .executor import Executor, ExecutorUnavailable
from .models import ArtifactResponse, JobRequest, JobResponse, JobStatus


class JobManager:
    def __init__(
        self,
        database: Database,
        cas: ContentAddressedStore,
        executor: Executor,
        *,
        max_artifact_bytes: int,
        max_job_artifacts_bytes: int,
        max_job_artifact_count: int,
        max_job_log_bytes: int,
        max_concurrent_jobs: int,
    ) -> None:
        self.database = database
        self.cas = cas
        self.executor = executor
        self.max_artifact_bytes = max_artifact_bytes
        self.max_job_artifacts_bytes = max_job_artifacts_bytes
        self.max_job_artifact_count = max_job_artifact_count
        self.max_job_log_bytes = max_job_log_bytes
        self.slots = asyncio.Semaphore(max_concurrent_jobs)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.changed: dict[str, asyncio.Condition] = {}
        self.closed = False
        self.shutting_down = False

    async def start(self) -> None:
        self.database.recover_interrupted_jobs()
        for job_id in self.database.queued_job_ids():
            self.schedule(job_id)

    async def stop(self) -> None:
        self.closed = True
        self.shutting_down = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def submit(self, token_id: str, request: JobRequest) -> tuple[JobResponse, bool]:
        if self.database.snapshot_manifest(request.snapshot_digest) is None:
            raise SnapshotNotFound(request.snapshot_digest)
        if request.target_architecture not in self.executor.target_architectures:
            supported = ", ".join(self.executor.target_architectures)
            raise ValueError(
                f"target architecture {request.target_architecture} is unavailable; "
                f"supported: {supported}"
            )
        canonical = request.model_dump_json(exclude_none=True)
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()
        job, replayed = self.database.create_job(
            job_id=uuid.uuid4().hex,
            token_id=token_id,
            request=request,
            request_hash=request_hash,
        )
        if not replayed:
            self.schedule(job.id)
        return job, replayed

    def schedule(self, job_id: str) -> None:
        if self.closed or job_id in self.tasks:
            return
        task = asyncio.create_task(self._run(job_id), name=f"forge-job-{job_id}")
        self.tasks[job_id] = task
        task.add_done_callback(lambda _task: self.tasks.pop(job_id, None))

    async def cancel(self, job_id: str, token_id: str) -> JobResponse:
        job = self.database.get_job_for_token(job_id, token_id)
        if job is None:
            raise JobNotFound(job_id)
        if job.status.terminal:
            return job
        task = self.tasks.get(job_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        current = self.database.get_job(job_id)
        assert current is not None
        if not current.status.terminal:
            self.database.update_job(job_id, JobStatus.CANCELLED, error="cancelled by client")
            await self._notify(job_id)
        result = self.database.get_job(job_id)
        assert result is not None
        return result

    async def _run(self, job_id: str) -> None:
        async with self.slots:
            await self._run_in_slot(job_id)

    async def _run_in_slot(self, job_id: str) -> None:
        job = self.database.get_job(job_id)
        if job is None or job.status != JobStatus.QUEUED:
            return
        owner_token_id = self.database.job_owner_token_id(job_id)
        if owner_token_id is None:
            self.database.update_job(job_id, JobStatus.FAILED, error="job owner disappeared")
            await self._notify(job_id)
            return
        manifest = self.database.snapshot_manifest(job.request.snapshot_digest)
        if manifest is None:
            self.database.update_job(job_id, JobStatus.FAILED, error="snapshot disappeared")
            await self._notify(job_id)
            return
        temporary = Path(tempfile.mkdtemp(prefix=f"forge-{job_id}-"))
        workspace = temporary / "workspace"
        try:
            await asyncio.to_thread(self.cas.materialize, manifest, workspace)
            self.database.update_job(job_id, JobStatus.RUNNING)
            await self._notify(job_id)

            log_bytes = 0
            log_truncated = False

            async def emit(event_type: str, data: dict[str, object]) -> None:
                nonlocal log_bytes, log_truncated
                if event_type == "output":
                    encoded = str(data.get("text", "")).encode(errors="replace")
                    if log_bytes + len(encoded) > self.max_job_log_bytes:
                        if not log_truncated:
                            self.database.append_event(
                                job_id,
                                "log_truncated",
                                {"limit_bytes": self.max_job_log_bytes},
                            )
                            log_truncated = True
                        return
                    log_bytes += len(encoded)
                self.database.append_event(job_id, event_type, data)
                await self._notify(job_id)

            result = await self.executor.run(owner_token_id, job_id, job.request, workspace, emit)
            await asyncio.to_thread(
                self._collect_artifacts, job_id, job.request.artifact_globs, workspace
            )
            status = JobStatus.SUCCEEDED if result.exit_code == 0 else JobStatus.FAILED
            self.database.update_job(job_id, status, exit_code=result.exit_code)
        except asyncio.CancelledError:
            if self.shutting_down:
                self.database.update_job(job_id, JobStatus.QUEUED)
                self.database.append_event(
                    job_id,
                    "suspended",
                    {"reason": "runner shutdown; job returned to durable queue"},
                )
            else:
                self.database.update_job(job_id, JobStatus.CANCELLED, error="cancelled by client")
            raise
        except (
            ExecutorUnavailable,
            OSError,
            ValueError,
            BlobTooLarge,
            StorageQuotaExceeded,
        ) as error:
            self.database.update_job(job_id, JobStatus.FAILED, error=str(error))
        except Exception as error:  # defensive job boundary; details stay local
            self.database.update_job(
                job_id, JobStatus.FAILED, error=f"internal executor error: {type(error).__name__}"
            )
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
            await self._notify(job_id)

    def _collect_artifacts(self, job_id: str, patterns: list[str], workspace: Path) -> None:
        seen: set[Path] = set()
        total_bytes = 0
        total_count = 0
        for pattern in patterns:
            self._validate_artifact_glob(pattern)
            for candidate in workspace.glob(pattern):
                if candidate in seen or candidate.is_symlink() or not candidate.is_file():
                    continue
                seen.add(candidate)
                total_count += 1
                if total_count > self.max_job_artifact_count:
                    raise StorageQuotaExceeded("per-job artifact count quota exceeded")
                resolved = candidate.resolve()
                if workspace.resolve() not in resolved.parents:
                    continue
                size = resolved.stat().st_size
                if size > self.max_artifact_bytes:
                    raise BlobTooLarge
                total_bytes += size
                if total_bytes > self.max_job_artifacts_bytes:
                    raise BlobTooLarge
                digest, stored_size = self.cas.put_file(resolved)
                name = candidate.relative_to(workspace).as_posix()
                media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                self.database.add_artifact(job_id, name, digest, stored_size, media_type)
                self.database.append_event(
                    job_id,
                    "artifact",
                    {"name": name, "digest": digest, "size": stored_size},
                )

    @staticmethod
    def _validate_artifact_glob(pattern: str) -> None:
        if not pattern or "\x00" in pattern or "\\" in pattern:
            raise ValueError("invalid artifact glob")
        path = PurePosixPath(pattern)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("artifact glob must remain within the workspace")

    async def _notify(self, job_id: str) -> None:
        condition = self.changed.setdefault(job_id, asyncio.Condition())
        async with condition:
            condition.notify_all()

    async def event_stream(self, job_id: str, *, after: int, follow: bool) -> AsyncIterator[bytes]:
        if self.database.get_job(job_id) is None:
            raise JobNotFound(job_id)
        cursor = after
        while True:
            events = self.database.events_after(job_id, cursor)
            for event in events:
                cursor = event.sequence
                payload = event.model_dump_json()
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n".encode()
            job = self.database.get_job(job_id)
            if not follow or job is None or (job.status.terminal and not events):
                return
            condition = self.changed.setdefault(job_id, asyncio.Condition())
            try:
                async with condition:
                    if self.database.events_after(job_id, cursor):
                        continue
                    await asyncio.wait_for(condition.wait(), timeout=15)
            except TimeoutError:
                yield b": keep-alive\n\n"

    def artifacts(self, job_id: str) -> list[ArtifactResponse]:
        if self.database.get_job(job_id) is None:
            raise JobNotFound(job_id)
        return [
            ArtifactResponse(
                name=str(row["name"]),
                digest=str(row["digest"]),
                size=int(row["size"]),
                media_type=str(row["media_type"]),
            )
            for row in self.database.artifacts_for_job(job_id)
        ]


class JobNotFound(Exception):
    pass


class SnapshotNotFound(Exception):
    pass


__all__ = ["IdempotencyConflict", "JobManager", "JobNotFound", "SnapshotNotFound"]
