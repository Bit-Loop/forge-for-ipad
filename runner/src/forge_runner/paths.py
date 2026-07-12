from __future__ import annotations

from pathlib import Path, PurePosixPath


def safe_relative_path(raw: str, *, allow_dot: bool = False) -> PurePosixPath:
    if not raw or "\x00" in raw or "\\" in raw:
        raise ValueError("path is empty or contains an invalid character")
    path = PurePosixPath(raw)
    if raw == ".":
        if allow_dot:
            return path
        raise ValueError("dot is not a snapshot path")
    if raw != path.as_posix():
        raise ValueError("path must be normalized")
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        if allow_dot and raw == ".":
            return PurePosixPath(".")
        raise ValueError("path must be normalized and relative")
    return path


def safe_symlink_target(link_path: PurePosixPath, target_raw: str) -> str:
    if not target_raw or "\x00" in target_raw or "\\" in target_raw:
        raise ValueError("symlink target is invalid")
    target = PurePosixPath(target_raw)
    if target.is_absolute() or target_raw != target.as_posix():
        raise ValueError("symlink targets must be normalized and relative")
    stack = list(link_path.parent.parts)
    for part in target.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                raise ValueError("symlink target escapes the snapshot")
            stack.pop()
        else:
            stack.append(part)
    return target_raw


def ensure_beneath(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve(strict=False)
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError("path escapes the workspace")
    return candidate_resolved


def safe_mode(mode: int, *, directory: bool = False) -> int:
    del directory
    return mode & 0o777
