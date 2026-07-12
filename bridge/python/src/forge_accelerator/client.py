from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .models import (
    Capabilities,
    ComputeUnits,
    Job,
    JobEventPage,
    JsonObject,
    MetalBuffer,
    ScratchReference,
    Tensor,
)

DEFAULT_ENDPOINT = "http://10.0.2.2:4777/accelerator/v1"
PROTOCOL_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response: ...


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


class UrllibTransport:
    def __init__(self, timeout: float = 30) -> None:
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_RejectRedirects)

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme, parsed.hostname, parsed.port) != ("http", "10.0.2.2", 4777):
            raise ValueError("transport refused a non-guest authority")
        request = urllib.request.Request(  # noqa: S310 - fixed HTTP guest authority above
            url, data=body, headers=dict(headers), method=method
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return Response(response.status, dict(response.headers.items()), response.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, dict(error.headers.items()), error.read())


class ProtocolError(RuntimeError):
    pass


class BridgeError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retriable: bool = False,
        request_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.retriable = retriable
        self.request_id = request_id
        self.details = details or {}


class AcceleratorClient:
    def __init__(
        self,
        token: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        transport: Transport | None = None,
        allow_non_guest_endpoint: bool = False,
    ) -> None:
        if len(token) < 32 or any(character.isspace() for character in token):
            raise ValueError(
                "token must be an opaque value of at least 32 non-whitespace characters"
            )
        normalized = endpoint.rstrip("/")
        if normalized != DEFAULT_ENDPOINT and not allow_non_guest_endpoint:
            raise ValueError(
                "non-guest endpoint refused; opt in only for an isolated test transport"
            )
        parsed = urllib.parse.urlsplit(normalized)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("endpoint must not contain credentials, query, or fragment")
        self.endpoint = normalized
        self._token = token
        self._transport = transport or UrllibTransport()

    def capabilities(self) -> Capabilities:
        value = self._request("GET", "/capabilities")
        capabilities = Capabilities.from_wire(value)
        if capabilities.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                f"host protocol {capabilities.protocol_version!r} is incompatible with "
                f"{PROTOCOL_VERSION!r}"
            )
        return capabilities

    def verify_scratch(self, object: ScratchReference) -> ScratchReference:
        return ScratchReference.from_wire(
            self._request("POST", "/scratch/verify", {"object": object.to_wire()})
        )

    def compile_coreml(
        self,
        source: ScratchReference,
        format: str,
        compute_units: ComputeUnits | None = None,
    ) -> Job:
        if format != "mlmodel":
            raise ValueError("Forge accelerator v1 supports only regular-file mlmodel input")
        body: JsonObject = {"source": source.to_wire(), "format": format}
        if compute_units is not None:
            body["compute_units"] = compute_units.value
        return Job.from_wire(self._request("POST", "/coreml/compilations", body))

    def predict_coreml(
        self,
        model_id: str,
        inputs: Mapping[str, Tensor],
        *,
        compute_units: ComputeUnits | None = None,
        output_delivery: str = "auto",
        max_inline_bytes: int | None = None,
    ) -> Job:
        self._uuid(model_id, "model_id")
        body: JsonObject = {
            "model_id": model_id,
            "inputs": {name: tensor.to_wire() for name, tensor in inputs.items()},
            "output_delivery": output_delivery,
        }
        if compute_units is not None:
            body["compute_units"] = compute_units.value
        if max_inline_bytes is not None:
            body["max_inline_bytes"] = max_inline_bytes
        return Job.from_wire(self._request("POST", "/coreml/predictions", body))

    def release_coreml(self, model_id: str) -> bool:
        value = self._request("DELETE", f"/coreml/models/{self._uuid(model_id, 'model_id')}")
        return bool(value["released"])

    def compile_metal(
        self,
        source: str | ScratchReference,
        *,
        language_version: str | None = None,
        fast_math: bool = True,
        macros: Mapping[str, str | int | float | bool] | None = None,
    ) -> Job:
        source_value: JsonObject = (
            {"storage": "inline", "text": source}
            if isinstance(source, str)
            else {"storage": "scratch", "object": source.to_wire()}
        )
        body: JsonObject = {"source": source_value, "fast_math": fast_math}
        if language_version is not None:
            body["language_version"] = language_version
        if macros:
            body["macros"] = dict(macros)
        return Job.from_wire(self._request("POST", "/metal/libraries", body))

    def dispatch_metal(
        self,
        library_id: str,
        function: str,
        grid: Sequence[int],
        threadgroup: Sequence[int],
        buffers: Sequence[MetalBuffer],
        *,
        constants: Mapping[str, str | int | float | bool] | None = None,
        output_delivery: str = "auto",
    ) -> Job:
        self._uuid(library_id, "library_id")
        if len(grid) != 3 or len(threadgroup) != 3:
            raise ValueError("grid and threadgroup must each contain exactly three dimensions")
        body: JsonObject = {
            "library_id": library_id,
            "function": function,
            "grid": list(grid),
            "threadgroup": list(threadgroup),
            "buffers": [buffer.to_wire() for buffer in buffers],
            "output_delivery": output_delivery,
        }
        if constants:
            body["constants"] = dict(constants)
        return Job.from_wire(self._request("POST", "/metal/dispatches", body))

    def release_metal(self, library_id: str) -> bool:
        value = self._request("DELETE", f"/metal/libraries/{self._uuid(library_id, 'library_id')}")
        return bool(value["released"])

    def job(self, job_id: str) -> Job:
        return Job.from_wire(self._request("GET", f"/jobs/{self._uuid(job_id, 'job_id')}"))

    def cancel(self, job_id: str) -> Job:
        return Job.from_wire(self._request("DELETE", f"/jobs/{self._uuid(job_id, 'job_id')}"))

    def events(self, job_id: str, *, after: int = 0, wait_seconds: float = 0) -> JobEventPage:
        if after < 0 or not 0 <= wait_seconds <= 30:
            raise ValueError("after must be nonnegative and wait_seconds must be between 0 and 30")
        query = urllib.parse.urlencode({"after": after, "wait_seconds": wait_seconds})
        value = self._request("GET", f"/jobs/{self._uuid(job_id, 'job_id')}/events?{query}")
        return JobEventPage.from_wire(value)

    def wait(self, job_id: str, *, timeout: float | None = None, poll_seconds: float = 0.25) -> Job:
        start = time.monotonic()
        while True:
            job = self.job(job_id)
            if job.terminal:
                return job
            if timeout is not None and time.monotonic() - start >= timeout:
                raise TimeoutError(f"job {job_id} did not finish within {timeout:g}s")
            time.sleep(poll_seconds)

    def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        request_id = str(uuid.uuid4())
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/json, application/problem+json",
            "Authorization": f"Bearer {self._token}",
            "X-Forge-Protocol-Version": PROTOCOL_VERSION,
            "X-Request-ID": request_id,
            "User-Agent": "forge-accelerator-python/1.0",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        response = self._transport.request(method, f"{self.endpoint}{path}", headers, payload)
        try:
            decoded = json.loads(response.body) if response.body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolError(f"bridge returned non-JSON HTTP {response.status}") from error
        if not isinstance(decoded, dict):
            raise ProtocolError("bridge response must be a JSON object")
        if 200 <= response.status < 300:
            return decoded
        error_value = decoded.get("error", {})
        if not isinstance(error_value, dict):
            error_value = {}
        raise BridgeError(
            response.status,
            str(error_value.get("code", "internal")),
            str(error_value.get("message", f"HTTP {response.status}")),
            retriable=bool(error_value.get("retriable", False)),
            request_id=str(error_value.get("request_id", request_id)),
            details=(
                error_value.get("details") if isinstance(error_value.get("details"), dict) else None
            ),
        )

    @staticmethod
    def _uuid(value: str, name: str) -> str:
        try:
            return str(uuid.UUID(value))
        except ValueError as error:
            raise ValueError(f"{name} must be a UUID") from error
