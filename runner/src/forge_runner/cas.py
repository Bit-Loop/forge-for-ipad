from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from .models import SnapshotEntry
from .paths import safe_mode, safe_relative_path, safe_symlink_target


class ContentAddressedStore:
    def __init__(self, root: Path, *, max_blob_bytes: int, max_total_bytes: int) -> None:
        self.root = root
        self.max_blob_bytes = max_blob_bytes
        self.max_total_bytes = max_total_bytes
        self._usage_lock = threading.Lock()
        self._used_bytes = 0

    def initialize(self) -> None:
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)
        for temporary in (self.root / "tmp").iterdir():
            if temporary.is_file() or temporary.is_symlink():
                temporary.unlink(missing_ok=True)
            elif temporary.is_dir():
                shutil.rmtree(temporary)
        with self._usage_lock:
            self._used_bytes = sum(
                path.stat().st_size for path in (self.root / "blobs").glob("*/*") if path.is_file()
            )
            if self._used_bytes > self.max_total_bytes:
                raise StorageQuotaExceeded("CAS already exceeds its aggregate storage quota")

    def blob_path(self, digest: str) -> Path:
        return self.root / "blobs" / digest[:2] / digest[2:]

    def has(self, digest: str) -> bool:
        return self.blob_path(digest).is_file()

    def size(self, digest: str) -> int | None:
        try:
            return self.blob_path(digest).stat().st_size
        except FileNotFoundError:
            return None

    async def put_stream(self, digest: str, chunks: AsyncIterator[bytes]) -> tuple[bool, int]:
        existing = self.size(digest)
        if existing is not None:
            size = 0
            hasher = hashlib.sha256()
            async for chunk in chunks:
                size += len(chunk)
                if size > self.max_blob_bytes:
                    raise BlobTooLarge
                hasher.update(chunk)
            if hasher.hexdigest() != digest:
                raise DigestMismatch
            return False, existing
        fd, temporary_name = tempfile.mkstemp(dir=self.root / "tmp")
        temporary = Path(temporary_name)
        size = 0
        reserved = 0
        hasher = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as output:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_blob_bytes:
                        raise BlobTooLarge
                    self._reserve(len(chunk))
                    reserved += len(chunk)
                    hasher.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if hasher.hexdigest() != digest:
                raise DigestMismatch
            destination = self.blob_path(digest)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                self._release(reserved)
                return False, destination.stat().st_size
            self._fsync_directory(destination.parent)
            self._fsync_directory(destination.parent.parent)
            return True, size
        except Exception:
            self._release(reserved)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def put_file(self, path: Path) -> tuple[str, int]:
        hasher = hashlib.sha256()
        size = 0
        reserved = 0
        fd, temporary_name = tempfile.mkstemp(dir=self.root / "tmp")
        temporary = Path(temporary_name)
        try:
            with path.open("rb") as source, os.fdopen(fd, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_blob_bytes:
                        raise BlobTooLarge
                    self._reserve(len(chunk))
                    reserved += len(chunk)
                    hasher.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            digest = hasher.hexdigest()
            destination = self.blob_path(digest)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                self._release(reserved)
                return digest, destination.stat().st_size
            self._fsync_directory(destination.parent)
            self._fsync_directory(destination.parent.parent)
            return digest, size
        except Exception:
            self._release(reserved)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    @property
    def used_bytes(self) -> int:
        with self._usage_lock:
            return self._used_bytes

    def _reserve(self, size: int) -> None:
        with self._usage_lock:
            if self._used_bytes + size > self.max_total_bytes:
                raise StorageQuotaExceeded("CAS aggregate storage quota exceeded")
            self._used_bytes += size

    def _release(self, size: int) -> None:
        with self._usage_lock:
            self._used_bytes = max(0, self._used_bytes - size)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def validate_manifest(self, entries: list[SnapshotEntry]) -> list[SnapshotEntry]:
        paths: dict[str, str] = {}
        normalized: list[SnapshotEntry] = []
        for entry in sorted(entries, key=lambda item: item.path):
            path = safe_relative_path(entry.path)
            canonical = path.as_posix()
            if canonical in paths:
                raise InvalidManifest(f"duplicate path: {canonical}")
            for parent in path.parents:
                if str(parent) == ".":
                    continue
                parent_kind = paths.get(parent.as_posix())
                if parent_kind is not None and parent_kind != "directory":
                    raise InvalidManifest(f"non-directory parent: {parent}")
            if entry.kind == "file":
                assert entry.digest is not None and entry.size is not None
                actual = self.size(entry.digest)
                if actual is None:
                    raise MissingBlob(entry.digest)
                if actual != entry.size:
                    raise InvalidManifest(f"size mismatch for {canonical}")
            elif entry.kind == "symlink":
                assert entry.target is not None
                safe_symlink_target(path, entry.target)
            paths[canonical] = entry.kind
            normalized.append(entry.model_copy(update={"path": canonical}))
        return normalized

    @staticmethod
    def canonical_manifest(entries: list[SnapshotEntry]) -> tuple[str, str]:
        manifest_json = json.dumps(
            [entry.model_dump(mode="json", exclude_none=True) for entry in entries],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(manifest_json.encode()).hexdigest(), manifest_json

    def materialize(self, entries: list[dict[str, object]], destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        directories = [entry for entry in entries if entry["kind"] == "directory"]
        other = [entry for entry in entries if entry["kind"] != "directory"]
        for entry in sorted(directories, key=lambda item: str(item["path"])):
            relative = safe_relative_path(str(entry["path"]))
            target = destination.joinpath(*relative.parts)
            target.mkdir(parents=True, exist_ok=False)
            target.chmod(0o700)
        for entry in sorted(other, key=lambda item: str(item["path"])):
            relative = safe_relative_path(str(entry["path"]))
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry["kind"] == "file":
                source = self.blob_path(str(entry["digest"]))
                if not source.is_file():
                    raise MissingBlob(str(entry["digest"]))
                # A workspace must never share an inode with immutable CAS data: build tools
                # routinely rewrite source files in place.
                shutil.copyfile(source, target)
                target.chmod(safe_mode(_entry_int(entry, "mode", 0o644)))
            else:
                link_target = str(entry["target"])
                safe_symlink_target(relative, link_target)
                target.symlink_to(link_target)
        for entry in sorted(
            directories,
            key=lambda item: len(safe_relative_path(str(item["path"])).parts),
            reverse=True,
        ):
            relative = safe_relative_path(str(entry["path"]))
            destination.joinpath(*relative.parts).chmod(
                safe_mode(_entry_int(entry, "mode", 0o755), directory=True)
            )


class DigestMismatch(Exception):
    pass


class BlobTooLarge(Exception):
    pass


class StorageQuotaExceeded(Exception):
    pass


class MissingBlob(Exception):
    pass


class InvalidManifest(Exception):
    pass


def _entry_int(entry: dict[str, object], key: str, default: int) -> int:
    value = entry.get(key, default)
    if not isinstance(value, int):
        raise InvalidManifest(f"{key} must be an integer")
    return value
