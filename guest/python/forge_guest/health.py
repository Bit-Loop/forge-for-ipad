"""Machine-readable Forge guest health checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import shutil
import socket
import subprocess

from . import API_VERSION


COMMANDS = {
    "compiler.c": ("cc", "gcc", "clang"),
    "compiler.cpp": ("c++", "g++", "clang++"),
    "rust": ("rustc",),
    "python": ("python3",),
    "docker": ("docker", "podman"),
    "lxc": ("lxc-start",),
    "desktop": ("startxfce4",),
    "ssh": ("sshd",),
}


def command_available(candidates: tuple[str, ...]) -> bool:
    return any(shutil.which(candidate) for candidate in candidates)


def systemd_active(unit: str) -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", unit], check=False
        ).returncode == 0
    except OSError:
        return False


def collect() -> dict:
    checks = {name: command_available(commands) for name, commands in COMMANDS.items()}
    checks["agent"] = systemd_active("forge-guest-agent.service")
    checks["cgroup2"] = Path("/sys/fs/cgroup/cgroup.controllers").exists()
    checks["virtio"] = any(Path("/sys/bus/virtio/devices").glob("virtio*"))
    return {
        "schema": API_VERSION,
        "healthy": checks["python"] and checks["ssh"],
        "hostname": socket.gethostname(),
        "architecture": platform.machine(),
        "kernel": platform.release(),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-guest-health")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = collect()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for name, ok in result["checks"].items():
            print(f"{'ok' if ok else 'missing':7} {name}")
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
