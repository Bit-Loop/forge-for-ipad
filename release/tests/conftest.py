from pathlib import Path

import pytest

from forge_release.crypto import generate_keypair, load_private_key, load_public_key


@pytest.fixture
def keys(tmp_path: Path):
    private = tmp_path / "secret" / "release.key"
    public = tmp_path / "public.json"
    swift = tmp_path / "EmbeddedReleaseKey.swift"
    metadata = generate_keypair(private, public, swift)
    return private, public, load_private_key(private), load_public_key(public), metadata, swift
