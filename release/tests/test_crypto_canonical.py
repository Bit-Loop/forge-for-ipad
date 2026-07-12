import json
import stat
from pathlib import Path

import pytest

from forge_release.canonical import CanonicalJSONError, canonical_bytes, read_json, write_json
from forge_release.crypto import SignatureError, sign_document, verify_document


def test_canonical_json_is_deterministic_and_atomic(tmp_path: Path):
    first = {"z": [3, "é"], "a": {"enabled": True}}
    assert canonical_bytes(first) == b'{"a":{"enabled":true},"z":[3,"\xc3\xa9"]}'
    path = tmp_path / "nested" / "document.json"
    write_json(path, first)
    assert read_json(path) == first
    assert not list(path.parent.glob("*.tmp"))


def test_canonical_json_rejects_floats():
    with pytest.raises(CanonicalJSONError):
        canonical_bytes({"unsafe": 0.1})


def test_key_generation_metadata_and_signature(keys):
    private_path, _, private, public, metadata, swift = keys
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o600
    assert len(metadata["key_id"]) == 64
    assert "PRIVATE" not in swift.read_text()
    signed = sign_document({"schema_version": 1, "value": "Forge"}, private)
    assert verify_document(signed, public)["value"] == "Forge"
    signed["value"] = "tampered"
    with pytest.raises(SignatureError):
        verify_document(signed, public)


def test_keygen_refuses_overwrite(keys):
    private_path, public_path, *_ = keys
    from forge_release.crypto import generate_keypair

    with pytest.raises(FileExistsError):
        generate_keypair(private_path, public_path)
