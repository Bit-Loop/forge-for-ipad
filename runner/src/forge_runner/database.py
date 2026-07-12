from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .cas import StorageQuotaExceeded
from .models import EventResponse, JobRequest, JobResponse, JobStatus


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Database:
    def __init__(
        self,
        path: Path,
        *,
        token_pepper: str = "",
        max_database_bytes: int = 4 * 1024**3,
        max_event_bytes: int = 2 * 1024**3,
        max_event_count: int = 5_000_000,
        max_artifact_storage_bytes: int = 64 * 1024**3,
        max_artifact_count: int = 1_000_000,
        max_artifact_metadata_bytes: int = 512 * 1024**2,
    ) -> None:
        self.path = path
        self.pepper = token_pepper.encode()
        self.max_database_bytes = max_database_bytes
        self.max_event_bytes = max_event_bytes
        self.max_event_count = max_event_count
        self.max_artifact_storage_bytes = max_artifact_storage_bytes
        self.max_artifact_count = max_artifact_count
        self.max_artifact_metadata_bytes = max_artifact_metadata_bytes

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    client_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    digest TEXT PRIMARY KEY,
                    manifest_json TEXT NOT NULL,
                    entry_count INTEGER NOT NULL,
                    total_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    token_id TEXT NOT NULL REFERENCES tokens(id),
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    exit_code INTEGER,
                    error TEXT,
                    UNIQUE(token_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    PRIMARY KEY(job_id, name)
                );
                CREATE TABLE IF NOT EXISTS storage_usage (
                    kind TEXT PRIMARY KEY,
                    item_count INTEGER NOT NULL,
                    content_bytes INTEGER NOT NULL,
                    metadata_bytes INTEGER NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO storage_usage(kind, item_count, content_bytes, "
                "metadata_bytes) SELECT 'events', COUNT(*), 0, "
                "COALESCE(SUM(length(CAST(type AS BLOB)) + length(CAST(data_json AS BLOB)) + "
                "length(CAST(created_at AS BLOB))), 0) FROM events"
            )
            connection.execute(
                "INSERT OR IGNORE INTO storage_usage(kind, item_count, content_bytes, "
                "metadata_bytes) SELECT 'artifacts', COUNT(*), COALESCE(SUM(size), 0), "
                "COALESCE(SUM(length(CAST(job_id AS BLOB)) + length(CAST(name AS BLOB)) + "
                "length(CAST(digest AS BLOB)) + length(CAST(media_type AS BLOB)) + 16), 0) "
                "FROM artifacts"
            )
            event_usage = connection.execute(
                "SELECT item_count, metadata_bytes FROM storage_usage WHERE kind='events'"
            ).fetchone()
            artifact_usage = connection.execute(
                "SELECT item_count, content_bytes, metadata_bytes FROM storage_usage "
                "WHERE kind='artifacts'"
            ).fetchone()
            assert event_usage is not None and artifact_usage is not None
            if (
                int(event_usage["item_count"]) > self.max_event_count
                or int(event_usage["metadata_bytes"]) > self.max_event_bytes
                or int(artifact_usage["item_count"]) > self.max_artifact_count
                or int(artifact_usage["content_bytes"]) > self.max_artifact_storage_bytes
                or int(artifact_usage["metadata_bytes"]) > self.max_artifact_metadata_bytes
            ):
                raise StorageQuotaExceeded("database storage already exceeds configured quotas")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        max_pages = max(1, self.max_database_bytes // page_size)
        effective_max_pages = int(
            connection.execute(f"PRAGMA max_page_count={max_pages}").fetchone()[0]
        )
        if effective_max_pages * page_size > self.max_database_bytes:
            connection.close()
            raise StorageQuotaExceeded("SQLite database already exceeds its storage quota")
        journal_limit = min(64 * 1024**2, max(1024**2, self.max_database_bytes // 16))
        connection.execute(f"PRAGMA journal_size_limit={journal_limit}")
        connection.execute("PRAGMA wal_autocheckpoint=1000")
        try:
            yield connection
        finally:
            connection.close()

    def transaction(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    def digest_secret(self, value: str) -> str:
        return hashlib.sha256(self.pepper + value.encode()).hexdigest()

    def add_pairing_code(self, code: str, expires_at: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO pairing_codes(code_hash, expires_at, used_at) "
                "VALUES (?, ?, NULL)",
                (self.digest_secret(code), expires_at),
            )

    def consume_pairing_code(self, code: str) -> bool:
        candidate = self.digest_secret(code)
        now = utc_now()
        with self.connect() as connection:
            self.transaction(connection)
            rows = connection.execute(
                "SELECT code_hash, expires_at, used_at FROM pairing_codes"
            ).fetchall()
            matched = next(
                (
                    row
                    for row in rows
                    if hmac.compare_digest(str(row["code_hash"]), candidate)
                    and row["used_at"] is None
                    and str(row["expires_at"]) > now
                ),
                None,
            )
            if matched is None:
                connection.rollback()
                return False
            connection.execute(
                "UPDATE pairing_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL",
                (now, candidate),
            )
            connection.commit()
            return True

    def add_token(self, token_id: str, token: str, client_name: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO tokens(id, token_hash, client_name, created_at) VALUES (?, ?, ?, ?)",
                (token_id, self.digest_secret(token), client_name, utc_now()),
            )

    def rotate_token(self, token_id: str, token: str, client_name: str) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE tokens SET token_hash=?, client_name=?, revoked_at=NULL "
                "WHERE id=? AND revoked_at IS NULL",
                (self.digest_secret(token), client_name, token_id),
            )
        return result.rowcount == 1

    def authenticate(self, token: str) -> tuple[str, str] | None:
        candidate = self.digest_secret(token)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, token_hash, client_name FROM tokens WHERE revoked_at IS NULL"
            ).fetchall()
        for row in rows:
            if hmac.compare_digest(str(row["token_hash"]), candidate):
                return str(row["id"]), str(row["client_name"])
        return None

    def put_snapshot(
        self, digest: str, manifest_json: str, entry_count: int, total_bytes: int
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO snapshots "
                "(digest, manifest_json, entry_count, total_bytes, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (digest, manifest_json, entry_count, total_bytes, utc_now()),
            )

    def snapshot_manifest(self, digest: str) -> list[dict[str, Any]] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM snapshots WHERE digest=?", (digest,)
            ).fetchone()
        return None if row is None else list(json.loads(str(row["manifest_json"])))

    def create_job(
        self,
        *,
        job_id: str,
        token_id: str,
        request: JobRequest,
        request_hash: str,
    ) -> tuple[JobResponse, bool]:
        request_json = request.model_dump_json()
        now = utc_now()
        with self.connect() as connection:
            self.transaction(connection)
            existing = connection.execute(
                "SELECT * FROM jobs WHERE token_id=? AND idempotency_key=?",
                (token_id, request.idempotency_key),
            ).fetchone()
            if existing is not None:
                connection.commit()
                if not hmac.compare_digest(str(existing["request_hash"]), request_hash):
                    raise IdempotencyConflict
                return self._job_from_row(existing), True
            connection.execute(
                "INSERT INTO jobs(id, token_id, idempotency_key, request_hash, request_json, "
                "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    token_id,
                    request.idempotency_key,
                    request_hash,
                    request_json,
                    JobStatus.QUEUED,
                    now,
                    now,
                ),
            )
            self._append_event(connection, job_id, "status", {"status": JobStatus.QUEUED})
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            connection.commit()
        assert row is not None
        return self._job_from_row(row), False

    def get_job(self, job_id: str) -> JobResponse | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return None if row is None else self._job_from_row(row)

    def get_job_for_token(self, job_id: str, token_id: str) -> JobResponse | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=? AND token_id=?", (job_id, token_id)
            ).fetchone()
        return None if row is None else self._job_from_row(row)

    def job_owner_token_id(self, job_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT token_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        return None if row is None else str(row["token_id"])

    def queued_job_ids(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs WHERE status=? ORDER BY created_at", (JobStatus.QUEUED,)
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def recover_interrupted_jobs(self) -> list[str]:
        recovered: list[str] = []
        with self.connect() as connection:
            self.transaction(connection)
            rows = connection.execute(
                "SELECT id FROM jobs WHERE status=?", (JobStatus.RUNNING,)
            ).fetchall()
            for row in rows:
                job_id = str(row["id"])
                recovered.append(job_id)
                connection.execute(
                    "UPDATE jobs SET status=?, updated_at=?, error=NULL WHERE id=?",
                    (JobStatus.QUEUED, utc_now(), job_id),
                )
                self._append_event(
                    connection,
                    job_id,
                    "recovered",
                    {"reason": "runner restarted; job returned to durable queue"},
                )
            connection.commit()
        return recovered

    def update_job(
        self,
        job_id: str,
        status: JobStatus,
        *,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            self.transaction(connection)
            connection.execute(
                "UPDATE jobs SET status=?, updated_at=?, exit_code=?, error=? WHERE id=?",
                (status, now, exit_code, error, job_id),
            )
            self._append_event(
                connection,
                job_id,
                "status",
                {"status": status, "exit_code": exit_code, "error": error},
            )
            connection.commit()

    def append_event(self, job_id: str, event_type: str, data: dict[str, object]) -> int | None:
        with self.connect() as connection:
            self.transaction(connection)
            sequence = self._append_event(connection, job_id, event_type, data)
            connection.commit()
        return sequence

    def _append_event(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        data: dict[str, object],
    ) -> int | None:
        data_json = json.dumps(data, separators=(",", ":"))
        created_at = utc_now()
        event_bytes = sum(len(value.encode()) for value in (event_type, data_json, created_at))
        usage = connection.execute(
            "SELECT item_count, metadata_bytes FROM storage_usage WHERE kind='events'"
        ).fetchone()
        assert usage is not None
        if (
            int(usage["item_count"]) + 1 > self.max_event_count
            or int(usage["metadata_bytes"]) + event_bytes > self.max_event_bytes
        ):
            return None
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM events WHERE job_id=?",
            (job_id,),
        ).fetchone()
        assert row is not None
        sequence = int(row["next"])
        connection.execute(
            "INSERT INTO events(job_id, sequence, type, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, sequence, event_type, data_json, created_at),
        )
        connection.execute(
            "UPDATE storage_usage SET item_count=item_count+1, "
            "metadata_bytes=metadata_bytes+? WHERE kind='events'",
            (event_bytes,),
        )
        return sequence

    def events_after(self, job_id: str, after: int) -> list[EventResponse]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT sequence, type, data_json, created_at FROM events "
                "WHERE job_id=? AND sequence>? ORDER BY sequence",
                (job_id, after),
            ).fetchall()
        return [
            EventResponse(
                sequence=int(row["sequence"]),
                type=str(row["type"]),
                data=dict(json.loads(str(row["data_json"]))),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def add_artifact(self, job_id: str, name: str, digest: str, size: int, media_type: str) -> None:
        with self.connect() as connection:
            self.transaction(connection)
            existing = connection.execute(
                "SELECT job_id, name, digest, size, media_type FROM artifacts "
                "WHERE job_id=? AND name=?",
                (job_id, name),
            ).fetchone()
            usage = connection.execute(
                "SELECT item_count, content_bytes, metadata_bytes FROM storage_usage "
                "WHERE kind='artifacts'"
            ).fetchone()
            assert usage is not None
            previous_size = 0 if existing is None else int(existing["size"])
            previous_metadata = 0 if existing is None else self._artifact_metadata_size(existing)
            metadata = sum(len(value.encode()) for value in (job_id, name, digest, media_type)) + 16
            next_count = int(usage["item_count"]) + (1 if existing is None else 0)
            next_content = int(usage["content_bytes"]) - previous_size + size
            next_metadata = int(usage["metadata_bytes"]) - previous_metadata + metadata
            if (
                next_count > self.max_artifact_count
                or next_content > self.max_artifact_storage_bytes
                or next_metadata > self.max_artifact_metadata_bytes
            ):
                connection.rollback()
                raise StorageQuotaExceeded("aggregate artifact storage quota exceeded")
            connection.execute(
                "INSERT OR REPLACE INTO artifacts(job_id, name, digest, size, media_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, name, digest, size, media_type),
            )
            connection.execute(
                "UPDATE storage_usage SET item_count=?, content_bytes=?, metadata_bytes=? "
                "WHERE kind='artifacts'",
                (next_count, next_content, next_metadata),
            )
            connection.commit()

    def artifact(self, digest: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT job_id, name, digest, size, media_type FROM artifacts "
                "WHERE digest=? ORDER BY job_id LIMIT 1",
                (digest,),
            ).fetchone()
        return cast("sqlite3.Row | None", row)

    def artifact_for_token(self, digest: str, token_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT artifacts.job_id, artifacts.name, artifacts.digest, artifacts.size, "
                "artifacts.media_type FROM artifacts JOIN jobs ON jobs.id=artifacts.job_id "
                "WHERE artifacts.digest=? AND jobs.token_id=? ORDER BY artifacts.job_id LIMIT 1",
                (digest, token_id),
            ).fetchone()
        return cast("sqlite3.Row | None", row)

    def artifacts_for_job(self, job_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name, digest, size, media_type FROM artifacts WHERE job_id=? ORDER BY name",
                (job_id,),
            ).fetchall()
        return cast("list[sqlite3.Row]", rows)

    def storage_usage(self, kind: str) -> tuple[int, int, int]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT item_count, content_bytes, metadata_bytes FROM storage_usage WHERE kind=?",
                (kind,),
            ).fetchone()
        if row is None:
            raise KeyError(kind)
        return int(row["item_count"]), int(row["content_bytes"]), int(row["metadata_bytes"])

    @staticmethod
    def _artifact_metadata_size(row: sqlite3.Row) -> int:
        return (
            sum(len(str(row[key]).encode()) for key in ("job_id", "name", "digest", "media_type"))
            + 16
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobResponse:
        return JobResponse(
            id=str(row["id"]),
            status=JobStatus(str(row["status"])),
            request=JobRequest.model_validate_json(str(row["request_json"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            exit_code=None if row["exit_code"] is None else int(row["exit_code"]),
            error=None if row["error"] is None else str(row["error"]),
        )


class IdempotencyConflict(Exception):
    pass
