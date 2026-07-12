from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse

from . import __version__
from .cas import (
    BlobTooLarge,
    ContentAddressedStore,
    DigestMismatch,
    InvalidManifest,
    MissingBlob,
    StorageQuotaExceeded,
)
from .config import Settings
from .database import Database, IdempotencyConflict
from .executor import Executor, PodmanExecutor, host_architecture
from .identity import load_or_create_instance_id
from .jobs import JobManager, JobNotFound, SnapshotNotFound
from .models import (
    ArtifactResponse,
    CapabilityResponse,
    Digest,
    HealthResponse,
    JobCreatedResponse,
    JobRequest,
    JobResponse,
    PairRequest,
    PairResponse,
    SnapshotRequest,
    SnapshotResponse,
)
from .paths import safe_relative_path
from .security import (
    PairingRateLimiter,
    PairingService,
    Principal,
    require_principal,
    websocket_principal,
)

LOGGER = logging.getLogger("forge_runner")
API = "/forge/v1"
PrincipalDep = Annotated[Principal, Depends(require_principal)]
AnnotatedAfter = Annotated[int, Query(ge=0)]
FollowQuery = Annotated[bool, Query()]


def create_app(settings: Settings | None = None, *, executor: Executor | None = None) -> FastAPI:
    configuration = settings or Settings.from_env()
    database = Database(
        configuration.database_path,
        token_pepper=configuration.token_pepper,
        max_database_bytes=configuration.max_database_bytes,
        max_event_bytes=configuration.max_event_storage_bytes,
        max_event_count=configuration.max_event_count,
        max_artifact_storage_bytes=configuration.max_artifact_storage_bytes,
        max_artifact_count=configuration.max_artifact_count,
        max_artifact_metadata_bytes=configuration.max_artifact_metadata_bytes,
    )
    cas = ContentAddressedStore(
        configuration.cas_dir,
        max_blob_bytes=configuration.max_blob_bytes,
        max_total_bytes=configuration.max_cas_bytes,
    )
    selected_executor = executor or PodmanExecutor(
        default_image=configuration.default_image,
        max_cache_bytes=configuration.max_cache_bytes,
    )
    pairing = PairingService(database)
    pairing_limiter = PairingRateLimiter()
    jobs = JobManager(
        database,
        cas,
        selected_executor,
        max_artifact_bytes=configuration.max_artifact_bytes,
        max_job_artifacts_bytes=configuration.max_job_artifacts_bytes,
        max_job_artifact_count=configuration.max_job_artifact_count,
        max_job_log_bytes=configuration.max_job_log_bytes,
        max_concurrent_jobs=configuration.max_concurrent_jobs,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.instance_id = load_or_create_instance_id(configuration.data_dir)
        database.initialize()
        cas.initialize()
        code = pairing.issue_code(configuration.pairing_code)
        app.state.pairing_code = code
        LOGGER.warning("Forge Runner one-time pairing code: %s (expires in 15 minutes)", code)
        await jobs.start()
        try:
            yield
        finally:
            await jobs.stop()

    app = FastAPI(
        title="Forge Runner",
        version=__version__,
        summary="Durable content-addressed execution for Forge for iPad",
        lifespan=lifespan,
    )
    app.state.settings = configuration
    app.state.database = database
    app.state.cas = cas
    app.state.executor = selected_executor
    app.state.jobs = jobs
    app.state.pairing = pairing
    app.state.instance_id = None

    @app.get("/healthz", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.post(API + "/pair", response_model=PairResponse)
    async def pair(payload: PairRequest, request: Request) -> PairResponse:
        client = request.client.host if request.client is not None else "unknown"
        retry_after = pairing_limiter.retry_after(client)
        if retry_after is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many failed pairing attempts",
                headers={"Retry-After": str(retry_after)},
            )
        result = pairing.pair(payload.code, payload.client_name, payload.existing_token_id)
        if result is None:
            pairing_limiter.failed(client)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired pairing code")
        pairing_limiter.succeeded(client)
        token_id, token = result
        return PairResponse(token=token, token_id=token_id)

    @app.get(API + "/capabilities", response_model=CapabilityResponse)
    async def capabilities(_principal: PrincipalDep) -> CapabilityResponse:
        if app.state.instance_id is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "runner is starting")
        architecture = host_architecture()
        available = await selected_executor.available()
        features = [
            "cas-v1",
            "durable-jobs",
            "event-replay-sse",
            "artifacts",
            "pty-boundary",
            "rootless-sandbox",
            "multi-step-jobs",
        ]
        if configuration.default_image is not None:
            features.append("default-image")
        return CapabilityResponse(
            instance_id=app.state.instance_id,
            server_version=__version__,
            host_architecture=architecture,
            target_architectures=list(selected_executor.target_architectures),
            executor=(
                selected_executor.name if available else f"{selected_executor.name}:unavailable"
            ),
            features=features,
        )

    @app.head(API + "/blobs/{digest}")
    async def head_blob(digest: Digest, _principal: PrincipalDep) -> Response:
        size = cas.size(digest)
        if size is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "blob not found")
        return Response(headers={"Content-Length": str(size), "ETag": f'"{digest}"'})

    @app.put(API + "/blobs/{digest}", status_code=status.HTTP_201_CREATED)
    async def put_blob(
        digest: Digest,
        request: Request,
        _principal: PrincipalDep,
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > configuration.max_blob_bytes:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "blob exceeds limit")
            except ValueError as error:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "invalid content-length"
                ) from error
        try:
            created, size = await cas.put_stream(digest, request.stream())
        except DigestMismatch as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "digest mismatch") from error
        except BlobTooLarge as error:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "blob exceeds limit") from error
        except StorageQuotaExceeded as error:
            raise HTTPException(507, "CAS aggregate storage quota exceeded") from error
        return Response(
            status_code=status.HTTP_201_CREATED if created else status.HTTP_204_NO_CONTENT,
            headers={"Content-Length": "0", "X-Forge-Blob-Size": str(size)},
        )

    @app.post(API + "/snapshots", response_model=SnapshotResponse)
    async def create_snapshot(
        request: SnapshotRequest, _principal: PrincipalDep
    ) -> SnapshotResponse:
        try:
            entries = cas.validate_manifest(request.entries)
            digest, manifest_json = cas.canonical_manifest(entries)
        except MissingBlob as error:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"snapshot references missing blob: {error}"
            ) from error
        except (InvalidManifest, ValueError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        total_bytes = sum(entry.size or 0 for entry in entries)
        database.put_snapshot(digest, manifest_json, len(entries), total_bytes)
        return SnapshotResponse(digest=digest, entry_count=len(entries), total_bytes=total_bytes)

    @app.post(
        API + "/jobs",
        response_model=JobCreatedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_job(request: JobRequest, principal: PrincipalDep) -> JobCreatedResponse:
        try:
            safe_relative_path(request.cwd, allow_dot=True)
            job, replayed = jobs.submit(principal.token_id, request)
        except SnapshotNotFound as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "snapshot not found") from error
        except IdempotencyConflict as error:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "idempotency key was used with a different request"
            ) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return JobCreatedResponse(job=job, replayed=replayed)

    @app.get(API + "/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str, principal: PrincipalDep) -> JobResponse:
        job = database.get_job_for_token(job_id, principal.token_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return job

    @app.delete(API + "/jobs/{job_id}", response_model=JobResponse)
    async def cancel_job(job_id: str, principal: PrincipalDep) -> JobResponse:
        try:
            return await jobs.cancel(job_id, principal.token_id)
        except JobNotFound as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found") from error

    @app.get(API + "/jobs/{job_id}/events")
    async def events(
        job_id: str,
        principal: PrincipalDep,
        after: AnnotatedAfter = 0,
        follow: FollowQuery = True,
    ) -> StreamingResponse:
        if database.get_job_for_token(job_id, principal.token_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return StreamingResponse(
            jobs.event_stream(job_id, after=after, follow=follow),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(API + "/jobs/{job_id}/artifacts", response_model=list[ArtifactResponse])
    async def list_artifacts(job_id: str, principal: PrincipalDep) -> list[ArtifactResponse]:
        if database.get_job_for_token(job_id, principal.token_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        try:
            return jobs.artifacts(job_id)
        except JobNotFound as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found") from error

    @app.get(API + "/artifacts/{digest}")
    async def get_artifact(digest: Digest, principal: PrincipalDep) -> FileResponse:
        artifact = database.artifact_for_token(digest, principal.token_id)
        path = cas.blob_path(digest)
        if artifact is None or not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
        return FileResponse(
            path,
            media_type=str(artifact["media_type"]),
            filename=Path(str(artifact["name"])).name,
            headers={"ETag": f'"{digest}"'},
        )

    @app.websocket(API + "/jobs/{job_id}/pty")
    async def job_pty(websocket: WebSocket, job_id: str) -> None:
        principal = websocket_principal(websocket)
        if principal is None:
            await websocket.close(code=4401, reason="invalid bearer token")
            return
        if database.get_job_for_token(job_id, principal.token_id) is None:
            await websocket.close(code=4404, reason="job not found")
            return
        session = await selected_executor.open_pty(job_id)
        if session is None:
            await websocket.close(code=4409, reason="PTY is not available for this job")
            return
        await websocket.accept()

        async def reader() -> None:
            while data := await session.receive():
                await websocket.send_bytes(data)

        async def writer() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if data is None and message.get("text") is not None:
                    data = str(message["text"]).encode()
                if data is not None:
                    await session.send(data)

        tasks = {asyncio.create_task(reader()), asyncio.create_task(writer())}
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await session.close()

    return app


app = create_app()
