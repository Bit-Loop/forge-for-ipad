from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from forge_runner.app import create_app
from forge_runner.config import Settings
from forge_runner.executor import FakeExecutor
from forge_runner.identity import InvalidInstanceID, load_or_create_instance_id


def test_instance_id_is_canonical_and_persists(tmp_path: Path) -> None:
    first = load_or_create_instance_id(tmp_path)
    second = load_or_create_instance_id(tmp_path)

    assert first == second
    assert UUID((tmp_path / "instance-id").read_text().strip()) == first
    assert (tmp_path / "instance-id").stat().st_mode & 0o777 == 0o600


def test_invalid_persisted_instance_id_fails_closed(tmp_path: Path) -> None:
    tmp_path.joinpath("instance-id").write_text("not-a-uuid\n")

    with pytest.raises(InvalidInstanceID, match="not a UUID"):
        load_or_create_instance_id(tmp_path)


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is unavailable")
def test_symbolic_link_instance_id_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("00000000-0000-0000-0000-000000000000\n")
    tmp_path.joinpath("instance-id").symlink_to(target)

    with pytest.raises(InvalidInstanceID, match="symbolic link"):
        load_or_create_instance_id(tmp_path)


def test_app_construction_does_not_create_the_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "not-created-at-import"
    app = create_app(
        Settings(data_dir=data_dir, pairing_code="123456", token_pepper="test-pepper"),
        executor=FakeExecutor(),
    )

    assert not data_dir.exists()
    with TestClient(app):
        assert data_dir.joinpath("instance-id").is_file()
