from pathlib import Path

import pytest

from forge_release.assets import AssetError, build_asset_pack, materialize_pack, verify_asset_pack
from forge_release.canonical import write_json
from forge_release.crypto import sign_document


def make_pack(tmp_path: Path, keys, version: str = "1.0.0"):
    _, _, private, public, *_ = keys
    source = tmp_path / f"source-{version}"
    source.mkdir()
    (source / "bin").mkdir()
    (source / "bin" / "clang").write_bytes(b"abcdefghij")
    (source / "README").write_bytes(b"runtime")
    chunks = tmp_path / "chunks"
    manifest = build_asset_pack(
        source,
        chunks,
        pack_id="ubuntu-core",
        version=version,
        minimum_runtime_abi="1",
        licenses=[{"name": "LLVM", "spdx": "Apache-2.0 WITH LLVM-exception"}],
        compatibility={"architecture": "arm64", "minimum_ipados": "27.0"},
        private_key=private,
        chunk_bytes=4,
    )
    return source, chunks, manifest, public


def test_pack_deduplicates_verifies_and_materializes(tmp_path: Path, keys):
    source, chunks, manifest, public = make_pack(tmp_path, keys)
    unsigned = verify_asset_pack(manifest, public, chunks)
    assert unsigned["expanded_size"] == 17
    assert len(list(chunks.iterdir())) == len(unsigned["chunks"])
    runtime = tmp_path / "runtime"
    result = materialize_pack(manifest, public, chunks, runtime)
    assert result["status"] == "activated"
    active = runtime / "packs" / "ubuntu-core" / "1.0.0"
    assert (active / "bin" / "clang").read_bytes() == (source / "bin" / "clang").read_bytes()
    assert materialize_pack(manifest, public, chunks, runtime)["status"] == "already_valid"


def test_materialization_repairs_partial_file_without_trusting_it(tmp_path: Path, keys):
    source, chunks, manifest, public = make_pack(tmp_path, keys)
    runtime = tmp_path / "runtime"
    staging = runtime / "packs" / "ubuntu-core" / ".1.0.0.staging" / "bin"
    staging.mkdir(parents=True)
    (staging / ".clang.part").write_bytes(b"abcdBROKEN")
    materialize_pack(manifest, public, chunks, runtime)
    assert (runtime / "packs" / "ubuntu-core" / "1.0.0" / "bin" / "clang").read_bytes() == b"abcdefghij"


def test_materialization_resumes_after_manifest_write_before_rename(tmp_path: Path, keys):
    _, chunks, manifest, public = make_pack(tmp_path, keys)
    runtime = tmp_path / "runtime"
    materialize_pack(manifest, public, chunks, runtime)
    pack_root = runtime / "packs" / "ubuntu-core"
    (pack_root / "1.0.0").rename(pack_root / ".1.0.0.staging")

    assert materialize_pack(manifest, public, chunks, runtime)["status"] == "activated"
    assert (pack_root / "1.0.0" / ".forge-pack-manifest.json").is_file()


def test_seed_never_replaces_valid_newer_pack(tmp_path: Path, keys):
    _, chunks_new, newer, public = make_pack(tmp_path, keys, "2.0.0")
    runtime = tmp_path / "runtime"
    materialize_pack(newer, public, chunks_new, runtime)
    _, chunks_old, older, _ = make_pack(tmp_path, keys, "1.0.0")
    result = materialize_pack(older, public, chunks_old, runtime)
    assert result == {"pack_id": "ubuntu-core", "status": "kept_newer", "version": "2.0.0"}


def test_corrupt_chunk_and_path_traversal_are_rejected(tmp_path: Path, keys):
    _, chunks, manifest, public = make_pack(tmp_path, keys)
    first = next(chunks.iterdir())
    first.write_bytes(b"bad")
    with pytest.raises(AssetError):
        verify_asset_pack(manifest, public, chunks)

    _, _, private, *_ = keys
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    unsigned["files"][0]["path"] = "../escape"
    with pytest.raises(AssetError):
        verify_asset_pack(sign_document(unsigned, private), public, chunks)


@pytest.mark.parametrize(
    "unsafe",
    [
        "",
        ".",
        "..",
        "../pack",
        "pack/child",
        r"pack\child",
        "/absolute",
        "c:/absolute",
        r"C:\absolute",
        "pack..child",
        "pack.-child",
        ".hidden",
        "trailing.",
        "UPPERCASE",
    ],
)
@pytest.mark.parametrize("field", ["pack_id", "version"])
def test_build_and_verify_reject_unsafe_pack_identity(tmp_path: Path, keys, field: str, unsafe: str):
    _, _, private, public, *_ = keys
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime").write_bytes(b"safe")
    arguments = {"pack_id": "safe-pack", "version": "1.0.0"}
    arguments[field] = unsafe
    with pytest.raises(AssetError):
        build_asset_pack(
            source,
            tmp_path / "rejected-chunks",
            minimum_runtime_abi="1",
            licenses=[],
            compatibility={},
            private_key=private,
            **arguments,
        )

    _, chunks, manifest, _ = make_pack(tmp_path, keys)
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    unsigned[field] = unsafe
    with pytest.raises(AssetError):
        verify_asset_pack(sign_document(unsigned, private), public, chunks)


def test_materialization_rejects_unsafe_active_version(tmp_path: Path, keys):
    _, chunks, manifest, public = make_pack(tmp_path, keys)
    runtime = tmp_path / "runtime"
    write_json(
        runtime / "active-packs.json",
        {"schema_version": 1, "packs": {"ubuntu-core": "../escape"}},
    )
    with pytest.raises(AssetError, match="active pack version"):
        materialize_pack(manifest, public, chunks, runtime)


def test_materialization_does_not_activate_undeclared_files(tmp_path: Path, keys):
    _, chunks, manifest, public = make_pack(tmp_path, keys)
    runtime = tmp_path / "runtime"
    assert materialize_pack(manifest, public, chunks, runtime)["status"] == "activated"
    active = runtime / "packs" / "ubuntu-core" / "1.0.0"
    (active / "undeclared").write_bytes(b"untrusted")

    assert materialize_pack(manifest, public, chunks, runtime)["status"] == "activated"
    assert not (active / "undeclared").exists()
