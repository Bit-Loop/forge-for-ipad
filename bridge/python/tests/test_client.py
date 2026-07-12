from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import uuid
from collections.abc import Mapping
from pathlib import Path

from forge_accelerator import AcceleratorClient, BridgeError, ComputeUnits, ScratchReference
from forge_accelerator.client import DEFAULT_ENDPOINT, PROTOCOL_VERSION, Response

TOKEN = "a" * 64
JOB_ID = "11111111-1111-4111-8111-111111111111"


class FakeTransport:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        self.requests.append((method, url, headers, body))
        return self.responses.pop(0)


def response(status: int, value: object) -> Response:
    return Response(status, {"Content-Type": "application/json"}, json.dumps(value).encode())


def job(state: str = "queued") -> dict[str, object]:
    return {
        "id": JOB_ID,
        "operation": "coreml_compile",
        "state": state,
        "created_at": "2026-07-12T00:00:00Z",
        "updated_at": "2026-07-12T00:00:00Z",
    }


class ClientTests(unittest.TestCase):
    def test_capabilities_sends_auth_and_version(self) -> None:
        value = {
            "protocol_version": "1.0",
            "server_version": "1.0.0",
            "boot_id": str(uuid.uuid4()),
            "compute_units": ["cpu", "all"],
            "coreml": {"available": True, "formats": ["mlmodel"]},
            "metal": {"available": True, "language_version": "4.0", "families": ["Apple10"]},
            "scratch": {"guest_root": "/run/forge/scratch", "requires_sha256": True},
            "limits": {
                "max_request_bytes": 1048576,
                "max_inline_bytes": 262144,
                "max_scratch_object_bytes": 1073741824,
                "max_tensor_rank": 8,
                "max_inputs": 64,
                "max_outputs": 64,
                "max_concurrent_jobs": 2,
                "max_model_handles": 4,
                "max_library_handles": 8,
                "max_model_bytes": 1073741824,
                "max_metal_source_bytes": 1048576,
                "max_buffer_bytes": 1073741824,
                "job_retention_seconds": 3600,
            },
        }
        transport = FakeTransport([response(200, value)])
        capabilities = AcceleratorClient(TOKEN, transport=transport).capabilities()
        self.assertEqual(capabilities.compute_units, (ComputeUnits.CPU, ComputeUnits.ALL))
        method, url, headers, body = transport.requests[0]
        self.assertEqual((method, url, body), ("GET", f"{DEFAULT_ENDPOINT}/capabilities", None))
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["X-Forge-Protocol-Version"], PROTOCOL_VERSION)
        self.assertNotIn(TOKEN, url)

    def test_compile_coreml_serializes_scratch_reference(self) -> None:
        transport = FakeTransport([response(202, job())])
        client = AcceleratorClient(TOKEN, transport=transport)
        reference = ScratchReference("models/a.mlmodel", "0" * 64, 12)
        accepted = client.compile_coreml(reference, "mlmodel", ComputeUnits.CPU_ANE)
        self.assertEqual(accepted.id, JOB_ID)
        request = json.loads(transport.requests[0][3] or b"{}")
        self.assertEqual(request["compute_units"], "cpu_ane")
        self.assertEqual(request["source"]["sha256"], "0" * 64)
        with self.assertRaisesRegex(ValueError, "only regular-file mlmodel"):
            client.compile_coreml(reference, "mlpackage")

    def test_error_is_structured(self) -> None:
        request_id = str(uuid.uuid4())
        transport = FakeTransport(
            [
                response(
                    413,
                    {
                        "error": {
                            "code": "limit_exceeded",
                            "message": "model is too large",
                            "retriable": False,
                            "request_id": request_id,
                        }
                    },
                )
            ]
        )
        with self.assertRaises(BridgeError) as raised:
            AcceleratorClient(TOKEN, transport=transport).capabilities()
        self.assertEqual(raised.exception.status, 413)
        self.assertEqual(raised.exception.code, "limit_exceeded")
        self.assertEqual(raised.exception.request_id, request_id)

    def test_release_validates_handle_and_is_idempotent(self) -> None:
        transport = FakeTransport([response(200, {"released": False})])
        client = AcceleratorClient(TOKEN, transport=transport)
        self.assertFalse(client.release_coreml(JOB_ID))
        self.assertEqual(transport.requests[0][1], f"{DEFAULT_ENDPOINT}/coreml/models/{JOB_ID}")

    def test_refuses_bad_endpoint_and_path_identifiers(self) -> None:
        with self.assertRaises(ValueError):
            AcceleratorClient(TOKEN, endpoint="http://192.168.1.2:4777/accelerator/v1")
        client = AcceleratorClient(TOKEN, transport=FakeTransport([]))
        with self.assertRaises(ValueError):
            client.job("../../capabilities")

    def test_scratch_reference_hashes_file_and_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tensors" / "input.bin"
            source.parent.mkdir()
            source.write_bytes(b"forge tensor")
            reference = ScratchReference.from_file(source, root)
            self.assertEqual(reference.relative_path, "tensors/input.bin")
            self.assertEqual(reference.size, 12)
            self.assertEqual(reference.sha256, hashlib.sha256(b"forge tensor").hexdigest())
            outside = root.parent / f"{root.name}-outside.bin"
            outside.write_bytes(b"no")
            try:
                with self.assertRaises(ValueError):
                    ScratchReference.from_file(outside, root)
            finally:
                outside.unlink()


if __name__ == "__main__":
    unittest.main()
