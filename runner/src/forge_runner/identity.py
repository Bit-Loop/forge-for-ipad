from __future__ import annotations

import errno
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

INSTANCE_ID_FILENAME = "instance-id"


class InvalidInstanceID(ValueError):
    """The persisted runner identity is missing, malformed, or unsafe to read."""


def parse_instance_id(value: str) -> UUID:
    candidate = value.strip()
    try:
        parsed = UUID(candidate)
    except ValueError as error:
        raise InvalidInstanceID("runner instance ID is not a UUID") from error
    if candidate != str(parsed):
        raise InvalidInstanceID("runner instance ID is not a canonical lowercase UUID")
    return parsed


def _read_instance_id(path: Path) -> UUID:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise InvalidInstanceID("runner instance ID must not be a symbolic link") from error
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64:
            raise InvalidInstanceID("runner instance ID must be a small regular file")
        with os.fdopen(descriptor, encoding="ascii") as identity_file:
            descriptor = -1
            return parse_instance_id(identity_file.read())
    except UnicodeError as error:
        raise InvalidInstanceID("runner instance ID is not ASCII") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_or_create_instance_id(data_dir: Path) -> UUID:
    """Return the durable UUID for one runner installation, creating it atomically."""

    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / INSTANCE_ID_FILENAME
    try:
        return _read_instance_id(path)
    except FileNotFoundError:
        pass

    instance_id = uuid4()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".instance-id.", dir=data_dir)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as identity_file:
            descriptor = -1
            identity_file.write(f"{instance_id}\n")
            identity_file.flush()
            os.fsync(identity_file.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return _read_instance_id(path)
        directory = os.open(data_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return instance_id
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
