"""Resolve and install declarative Forge package packs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from typing import Iterable


class PackError(ValueError):
    pass


def load_packs(directory: Path) -> dict[str, dict]:
    packs: dict[str, dict] = {}
    for path in sorted(directory.glob("*.toml")):
        with path.open("rb") as stream:
            pack = tomllib.load(stream)
        pack_id = pack.get("id")
        if pack.get("schema") != 1 or not isinstance(pack_id, str):
            raise PackError(f"invalid pack manifest: {path}")
        if pack_id in packs:
            raise PackError(f"duplicate pack id: {pack_id}")
        pack["_path"] = str(path)
        packs[pack_id] = pack
    return packs


def resolve(packs: dict[str, dict], selected: Iterable[str]) -> list[dict]:
    ordered: list[dict] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(pack_id: str) -> None:
        if pack_id in visited:
            return
        if pack_id in visiting:
            raise PackError(f"dependency cycle at {pack_id}")
        try:
            pack = packs[pack_id]
        except KeyError as error:
            raise PackError(f"unknown pack: {pack_id}") from error
        visiting.add(pack_id)
        for dependency in pack.get("depends", []):
            visit(dependency)
        visiting.remove(pack_id)
        visited.add(pack_id)
        ordered.append(pack)

    for pack_id in selected:
        visit(pack_id)
    return ordered


def detect_distro(os_release: Path = Path("/etc/os-release")) -> str:
    values: dict[str, str] = {}
    for raw in os_release.read_text().splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key] = value.strip('"')
    distro = values.get("ID", "")
    if distro == "ubuntu":
        return "apt"
    if distro in {"arch", "manjaro-arm", "manjaro"}:
        return "pacman"
    raise PackError(f"unsupported distribution: {distro or 'unknown'}")


def plan(directory: Path, selected: Iterable[str], manager: str) -> dict:
    resolved = resolve(load_packs(directory), selected)
    packages: list[str] = []
    python_tools: list[str] = []
    python_environment: list[str] = []
    python_binary_only: list[str] = []
    services: list[str] = []
    rust_components: list[str] = []
    rust_targets: list[str] = []
    cargo_tools: list[str] = []
    for pack in resolved:
        packages.extend(pack.get("packages", {}).get(manager, []))
        python_tools.extend(pack.get("python_tools", {}).get("packages", []))
        environment = pack.get("python_environment", {})
        environment_packages = environment.get("packages", [])
        if environment.get("binary_only"):
            python_binary_only.extend(environment_packages)
        else:
            python_environment.extend(environment_packages)
        services.extend(pack.get("services", {}).get("enable", []))
        rust_components.extend(pack.get("rustup", {}).get("components", []))
        rust_targets.extend(pack.get("rustup", {}).get("targets", []))
        cargo_tools.extend(pack.get("cargo_tools", {}).get("packages", []))
    return {
        "schema": 1,
        "manager": manager,
        "packs": [pack["id"] for pack in resolved],
        "packages": sorted(set(packages)),
        "python_tools": sorted(set(python_tools)),
        "python_environment": sorted(set(python_environment)),
        "python_binary_only": sorted(set(python_binary_only)),
        "services": sorted(set(services)),
        "rust_components": sorted(set(rust_components)),
        "rust_targets": sorted(set(rust_targets)),
        "cargo_tools": sorted(set(cargo_tools)),
    }


def run(argv: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(argv, check=True, env=env)


def install(plan_data: dict, *, install_system: bool = True, install_tools: bool = True) -> None:
    manager = plan_data["manager"]
    packages = plan_data["packages"]
    if os.geteuid() != 0:
        raise PackError("installation requires root")
    if install_system and manager == "apt":
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        run(["apt-get", "update"], env=env)
        if packages:
            run(["apt-get", "install", "-y", "--no-install-recommends", *packages], env=env)
    elif install_system and manager == "pacman":
        if packages:
            run(["pacman", "-S", "--needed", "--noconfirm", *packages])
    elif install_system:
        raise PackError(f"unsupported package manager: {manager}")

    if install_tools and (plan_data["python_tools"] or plan_data["python_environment"] or plan_data["python_binary_only"]):
        uv = shutil.which("uv")
        if not uv:
            raise PackError("Python tools requested but locked uv is not installed")
        uv_env = [
            "env",
            "HOME=/home/forge",
            "UV_TOOL_DIR=/home/forge/.local/share/uv/tools",
            "UV_TOOL_BIN_DIR=/home/forge/.local/bin",
        ]
        for package in plan_data["python_tools"]:
            run(["runuser", "-u", "forge", "--", *uv_env, uv, "tool", "install", "--force", package])
        if plan_data["python_environment"] or plan_data["python_binary_only"]:
            environment = "/home/forge/.venvs/forge-workstation"
            run(["runuser", "-u", "forge", "--", *uv_env, uv, "venv", "--clear", environment])
            if plan_data["python_environment"]:
                run([
                    "runuser", "-u", "forge", "--", *uv_env, uv, "pip", "install",
                    "--python", f"{environment}/bin/python", *plan_data["python_environment"],
                ])
            if plan_data["python_binary_only"]:
                binary_command = [
                    "runuser", "-u", "forge", "--", *uv_env, uv, "pip", "install",
                    "--only-binary", ":all:", "--python", f"{environment}/bin/python",
                    *plan_data["python_binary_only"],
                ]
                run([*binary_command[:11], "--dry-run", *binary_command[11:]])
                run(binary_command)

    cargo_home = Path("/home/forge/.cargo")
    rustup = cargo_home / "bin/rustup"
    cargo = cargo_home / "bin/cargo"
    rust_env = ["env", "HOME=/home/forge", f"CARGO_HOME={cargo_home}", "RUSTUP_HOME=/home/forge/.rustup"]
    if install_tools and (plan_data["rust_components"] or plan_data["rust_targets"] or plan_data["cargo_tools"]):
        if not rustup.is_file():
            raise PackError("Rust tools requested but locked rustup is not installed")
        if plan_data["rust_components"]:
            run(["runuser", "-u", "forge", "--", *rust_env, str(rustup), "component", "add", *plan_data["rust_components"]])
        if plan_data["rust_targets"]:
            run(["runuser", "-u", "forge", "--", *rust_env, str(rustup), "target", "add", *plan_data["rust_targets"]])
        for package in plan_data["cargo_tools"]:
            run(["runuser", "-u", "forge", "--", *rust_env, str(cargo), "install", "--locked", package])

    if install_system:
        for service in plan_data["services"]:
            run(["systemctl", "enable", service])


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="forge-pack")
    result.add_argument("--packs-dir", type=Path, default=Path("/usr/share/forge/packs"))
    result.add_argument("--manager", choices=("apt", "pacman"))
    result.add_argument("--json", action="store_true")
    result.add_argument("--install", action="store_true")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--system-only", action="store_true")
    mode.add_argument("--tools-only", action="store_true")
    result.add_argument("packs", nargs="+")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        data = plan(args.packs_dir, args.packs, args.manager or detect_distro())
        if args.install:
            install(data, install_system=not args.tools_only, install_tools=not args.system_only)
        if args.json:
            print(json.dumps(data, sort_keys=True))
        else:
            print("packs:", " ".join(data["packs"]))
            print("packages:", " ".join(data["packages"]))
        return 0
    except (OSError, PackError, subprocess.CalledProcessError) as error:
        print(f"forge-pack: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
