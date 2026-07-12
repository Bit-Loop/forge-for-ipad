"""Authenticated local lifecycle agent for a Forge Linux guest."""

from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import socketserver
import subprocess
from typing import Any

from . import API_VERSION
from .health import collect


MAX_REQUEST_BYTES = 1 << 20
SOCKET_PATH = Path("/run/forge/agent.sock")
TOKEN_PATH = Path("/run/forge/token")


class RequestError(ValueError):
    pass


def capabilities() -> dict[str, Any]:
    return {
        "schema": API_VERSION,
        "methods": ["capabilities", "checkpoint", "health", "ping"],
        "transport": "unix-jsonl",
        "execution_transport": "ssh",
    }


def dispatch(request: dict[str, Any], token: str) -> dict[str, Any]:
    if request.get("version") != API_VERSION:
        raise RequestError("unsupported protocol version")
    supplied = request.get("token")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, token):
        raise RequestError("authentication failed")
    request_id = request.get("id")
    method = request.get("method")
    if method == "ping":
        result: Any = {"pong": True}
    elif method == "health":
        result = collect()
    elif method == "capabilities":
        result = capabilities()
    elif method == "checkpoint":
        subprocess.run(["sync"], check=True)
        result = {"flushed": True}
    else:
        raise RequestError("unknown method")
    return {"version": API_VERSION, "id": request_id, "ok": True, "result": result}


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            self.respond(None, error="request too large")
            return
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise RequestError("request must be an object")
            response = dispatch(request, self.server.token)  # type: ignore[attr-defined]
        except (json.JSONDecodeError, OSError, RequestError, subprocess.CalledProcessError) as error:
            request_id = request.get("id") if isinstance(locals().get("request"), dict) else None
            self.respond(request_id, error=str(error))
            return
        self.wfile.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")

    def respond(self, request_id: Any, *, error: str) -> None:
        response = {"version": API_VERSION, "id": request_id, "ok": False, "error": error}
        self.wfile.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: str, token: str):
        self.token = token
        super().__init__(path, Handler)


def serve(socket_path: Path = SOCKET_PATH, token_path: Path = TOKEN_PATH) -> None:
    token = token_path.read_text().strip()
    if len(token) < 32:
        raise RequestError("boot token must contain at least 32 characters")
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    with Server(str(socket_path), token) as server:
        os.chmod(socket_path, 0o660)
        server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-guest-agent")
    parser.add_argument("--socket", type=Path, default=SOCKET_PATH)
    parser.add_argument("--token", type=Path, default=TOKEN_PATH)
    args = parser.parse_args(argv)
    serve(args.socket, args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
