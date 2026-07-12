from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import JobRequest
from .paths import ensure_beneath, safe_relative_path

Emit = Callable[[str, dict[str, object]], Awaitable[None]]
DEFAULT_CACHE_VOLUME_SIZE_BYTES = 2 * 1024**3


def normalize_architecture(value: str) -> str:
    architecture = value.lower()
    aliases = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
    try:
        return aliases[architecture]
    except KeyError as error:
        raise ExecutorUnavailable(f"unsupported host architecture: {value}") from error


def host_architecture() -> str:
    return normalize_architecture(platform.machine())


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_code: int


class PTYSession(Protocol):
    async def send(self, data: bytes) -> None: ...

    async def receive(self) -> bytes | None: ...

    async def close(self) -> None: ...


class Executor(Protocol):
    name: str
    target_architectures: tuple[str, ...]

    async def available(self) -> bool: ...

    async def run(
        self,
        owner_token_id: str,
        job_id: str,
        request: JobRequest,
        workspace: Path,
        emit: Emit,
    ) -> ExecutionResult: ...

    async def open_pty(self, job_id: str) -> PTYSession | None: ...


class PodmanExecutor:
    """Rootless Podman boundary. Images are local-only and root filesystems are immutable."""

    name = "podman-rootless"

    def __init__(
        self,
        *,
        default_image: str | None = None,
        max_cache_bytes: int = DEFAULT_CACHE_VOLUME_SIZE_BYTES,
    ) -> None:
        if max_cache_bytes <= 0:
            raise ValueError("max_cache_bytes must be positive")
        self.default_image = default_image
        self.max_cache_bytes = max_cache_bytes
        self.host_architecture = host_architecture()
        self.target_architectures: tuple[str, ...] = (self.host_architecture,)
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def available(self) -> bool:
        if shutil.which("podman") is None or os.geteuid() == 0:
            return False
        process = await asyncio.create_subprocess_exec(
            "podman",
            "info",
            "--format={{.Host.Security.Rootless}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        return process.returncode == 0 and stdout.strip().lower() == b"true"

    async def run(
        self,
        owner_token_id: str,
        job_id: str,
        request: JobRequest,
        workspace: Path,
        emit: Emit,
    ) -> ExecutionResult:
        requested_architecture = normalize_architecture(request.target_architecture)
        if requested_architecture != self.host_architecture:
            raise ExecutorUnavailable(
                f"target architecture {requested_architecture} does not match "
                f"Podman host architecture {self.host_architecture}"
            )
        image = request.image or self.default_image
        if image is None:
            raise ExecutorUnavailable("job request does not specify a container image")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}", image) is None:
            raise ExecutorUnavailable("job image reference is invalid")
        if request.secret_references:
            raise ExecutorUnavailable("secret references require a configured secret provider")
        cwd = safe_relative_path(request.cwd, allow_dot=True)
        host_cwd = ensure_beneath(workspace, workspace.joinpath(*cwd.parts))
        if not host_cwd.is_dir():
            raise ExecutorUnavailable(f"working directory does not exist: {request.cwd}")
        commands = request.steps or [request.argv or ["/bin/sh", "-lc", request.shell or ""]]
        cache_volume = self._cache_volume_name(owner_token_id, job_id)
        arguments = [
            "podman",
            "run",
            "--rm",
            "--replace",
            "--pull=never",
            f"--name=forge-{job_id}",
            "--userns=keep-id",
            "--read-only",
            "--cap-drop=all",
            "--security-opt=no-new-privileges",
            f"--cpus={request.limits.cpus}",
            f"--memory={request.limits.memory_mb}m",
            f"--pids-limit={request.limits.pids}",
            "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=1g",
            f"--volume={workspace}:/workspace:rw,Z",
            f"--volume={cache_volume}:/forge-cache:rw,U",
            "--env=HOME=/forge-cache/home",
            "--env=CARGO_HOME=/forge-cache/cargo",
            "--env=UV_CACHE_DIR=/forge-cache/uv",
            "--env=PIP_CACHE_DIR=/forge-cache/pip",
            "--env=CONAN_HOME=/forge-cache/conan",
            f"--workdir=/workspace/{cwd.as_posix()}",
        ]
        await emit("executor", {"name": self.name, "image": image})
        process: asyncio.subprocess.Process | None = None
        cache_cleanup_required = False
        cache_cleanup_failed = False

        def network_arguments(index: int) -> list[str]:
            boundary = request.network.networked_steps
            if request.network.enabled and (boundary is None or index < boundary):
                values = ["--network=slirp4netns:allow_host_loopback=false"]
                for port in request.network.published_ports:
                    host = "" if port.host_port is None else str(port.host_port)
                    values.append(
                        f"--publish=127.0.0.1:{host}:{port.container_port}/{port.protocol}"
                    )
                return values
            return ["--network=none"]

        async def relay(stream: asyncio.StreamReader, channel: str) -> None:
            while chunk := await stream.read(64 * 1024):
                await emit("output", {"channel": channel, "text": chunk.decode(errors="replace")})

        async def execute_steps() -> int:
            nonlocal process
            for index, command in enumerate(commands):
                await emit("step", {"index": index, "argv0": command[0]})
                process = await asyncio.create_subprocess_exec(
                    *arguments,
                    *network_arguments(index),
                    "--",
                    image,
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                self._processes[job_id] = process
                assert process.stdout is not None and process.stderr is not None
                await asyncio.gather(
                    relay(process.stdout, "stdout"),
                    relay(process.stderr, "stderr"),
                    process.wait(),
                )
                if process.returncode != 0:
                    return process.returncode or 1
            return 0

        try:
            await self._remove_cache_volume(cache_volume)
            cache_cleanup_required = True
            await self._create_cache_volume(cache_volume)
            async with asyncio.timeout(request.limits.timeout_seconds):
                exit_code = await execute_steps()
        except TimeoutError:
            await self._stop_container(job_id)
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            await emit("timeout", {"timeout_seconds": request.limits.timeout_seconds})
            exit_code = 124
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                await self._stop_container(job_id)
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        finally:
            self._processes.pop(job_id, None)
            if cache_cleanup_required:
                removed = await self._remove_cache_volume(cache_volume)
                if not removed:
                    cache_cleanup_failed = True
                    await emit("executor_cleanup_failed", {"resource": "job-cache"})
        if cache_cleanup_failed:
            raise ExecutorUnavailable("could not remove bounded job cache")
        return ExecutionResult(exit_code)

    @staticmethod
    def _cache_volume_name(owner_token_id: str, job_id: str) -> str:
        digest = hashlib.sha256(f"{owner_token_id}\0{job_id}".encode()).hexdigest()[:32]
        return f"forge-job-cache-{digest}"

    async def _create_cache_volume(self, name: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "podman",
            "volume",
            "create",
            "--driver=local",
            "--opt=type=tmpfs",
            "--opt=device=tmpfs",
            f"--opt=o=size={self.max_cache_bytes},mode=0700,nosuid,nodev",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise ExecutorUnavailable(f"could not create bounded job cache{suffix}")

    @staticmethod
    async def _remove_cache_volume(name: str) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                "podman",
                "volume",
                "rm",
                "--force",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            async with asyncio.timeout(10):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
            return False
        except OSError:
            return False
        return process.returncode == 0

    async def open_pty(self, job_id: str) -> PTYSession | None:
        # Deliberately isolated behind PTYSession. A future conmon attach implementation can
        # replace this without changing the API or job lifecycle.
        return None

    @staticmethod
    async def _stop_container(job_id: str) -> None:
        stopper = await asyncio.create_subprocess_exec(
            "podman",
            "stop",
            "--time=5",
            f"forge-{job_id}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(stopper.wait(), 8)
        except TimeoutError:
            stopper.kill()
            await stopper.wait()


class FakeExecutor:
    """Deterministic executor for contract tests. Never executes caller-provided code."""

    name = "fake"
    target_architectures: tuple[str, ...] = ("arm64", "amd64")

    def __init__(self) -> None:
        self._sessions: dict[str, EchoPTYSession] = {}

    async def available(self) -> bool:
        return True

    async def run(
        self,
        owner_token_id: str,
        job_id: str,
        request: JobRequest,
        workspace: Path,
        emit: Emit,
    ) -> ExecutionResult:
        await emit("executor", {"name": self.name})
        commands = request.steps or [request.argv or ["shell", request.shell or ""]]
        for index, argv in enumerate(commands):
            await emit("step", {"index": index, "argv0": argv[0]})
            operation = argv[1] if len(argv) > 1 else "success"
            if operation == "sleep":
                delay = min(float(argv[2]) if len(argv) > 2 else 0.05, 30)
                await asyncio.sleep(delay)
            elif operation == "write":
                if len(argv) < 4:
                    return ExecutionResult(64)
                relative = safe_relative_path(argv[2])
                target = ensure_beneath(workspace, workspace.joinpath(*relative.parts))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(argv[3])
                await emit("output", {"channel": "stdout", "text": f"wrote {relative}\n"})
            elif operation == "fail":
                await emit("output", {"channel": "stderr", "text": "intentional failure\n"})
                return ExecutionResult(2)
            else:
                await emit("output", {"channel": "stdout", "text": "fake executor completed\n"})
        self._sessions[job_id] = EchoPTYSession()
        return ExecutionResult(0)

    async def open_pty(self, job_id: str) -> PTYSession | None:
        return self._sessions.get(job_id)


class EchoPTYSession:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def send(self, data: bytes) -> None:
        await self.queue.put(data)

    async def receive(self) -> bytes | None:
        return await self.queue.get()

    async def close(self) -> None:
        await self.queue.put(None)


class ExecutorUnavailable(Exception):
    pass
