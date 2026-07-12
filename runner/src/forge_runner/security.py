from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Header, HTTPException, Request, WebSocket, status

from .database import Database


@dataclass(frozen=True, slots=True)
class Principal:
    token_id: str
    client_name: str


class PairingService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def issue_code(self, code: str | None = None, *, lifetime_minutes: int = 15) -> str:
        value = code or f"{secrets.randbelow(1_000_000):06d}"
        expires = datetime.now(UTC) + timedelta(minutes=lifetime_minutes)
        self.database.add_pairing_code(value, expires.isoformat(timespec="milliseconds"))
        return value

    def pair(
        self,
        code: str,
        client_name: str,
        existing_token_id: str | None = None,
    ) -> tuple[str, str] | None:
        if not self.database.consume_pairing_code(code):
            return None
        token = secrets.token_urlsafe(32)
        token_id = existing_token_id or secrets.token_hex(12)
        if existing_token_id is None:
            self.database.add_token(token_id, token, client_name)
        elif not self.database.rotate_token(token_id, token, client_name):
            return None
        return token_id, token


class PairingRateLimiter:
    """Small in-process brute-force guard for the short, human-entered pairing code."""

    def __init__(self, *, attempts: int = 5, window_seconds: int = 60) -> None:
        self.limit = attempts
        self.window = window_seconds
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def retry_after(self, client: str) -> int | None:
        now = time.monotonic()
        failures = self.failures[client]
        while failures and failures[0] <= now - self.window:
            failures.popleft()
        if len(failures) < self.limit:
            return None
        return max(1, int(self.window - (now - failures[0])))

    def failed(self, client: str) -> None:
        self.failures[client].append(time.monotonic())

    def succeeded(self, client: str) -> None:
        self.failures.pop(client, None)


def _bearer_value(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not value:
        return None
    return value


async def require_principal(
    request: Request, authorization: str | None = Header(default=None)
) -> Principal:
    token = _bearer_value(authorization)
    authenticated = None if token is None else request.app.state.database.authenticate(token)
    if authenticated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Principal(*authenticated)


def websocket_principal(websocket: WebSocket) -> Principal | None:
    # Tokens in query strings leak into proxies and access logs. Native Forge clients can and
    # must send the same Authorization header used by HTTP endpoints.
    token = _bearer_value(websocket.headers.get("authorization"))
    authenticated = None if token is None else websocket.app.state.database.authenticate(token)
    return None if authenticated is None else Principal(*authenticated)
