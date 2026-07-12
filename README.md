# Forge for iPad

Forge is a native, multiwindow iPadOS 27 development environment for the M5 iPad Pro. It is designed to combine a Files-visible workspace, a native IDE, an ARM64 Linux workstation, WASI execution, a durable LAN runner, and typed Core ML/Metal acceleration.

The project intentionally targets personal sideloading with a free Apple Personal Team. It does not claim App Store compatibility, unrestricted native iPadOS code execution, Hypervisor.framework access, transparent Neural Engine passthrough, or perpetual background execution.

## What works today

- A conventional arm64 iPadOS 27 IPA built on Linux with Swift 6.4/Xcode 27 SDK tooling.
- Native value-addressed multiwindow scenes and responsive 820/1180-point workspace breakpoints.
- Files-visible workspaces plus persistent external folder access for Working Copy, Textastic, and other Files providers.
- Recursive source navigation, atomic saves, and fsynced edit journals.
- C, C++, Rust, and Python project detection with structured fetch/lint/build/test/run plans.
- Bonjour runner discovery, one-time pairing, device-only Keychain credentials, content-addressed snapshots, durable remote jobs, resumable events, and artifact verification.
- iPadOS continued processing for user-triggered runner work, with persisted remote job IDs and event cursors for process-death recovery.
- Ed25519-signed runtime packs, resumable chunk installation, and a real Seed IPA assembler that preserves newer installed packs.
- Versioned guest lifecycle and Core ML/Metal bridge contracts with Python, Rust, C, and C++ guest SDKs.

The UTM/QEMU binary, promoted Ubuntu image, native terminal/LSP/debugger, and Core ML/Metal execution backend are still release blockers. The repository does not pretend those contracts are already a working on-device VM; see [known limitations](docs/known-limitations.md).

## Distribution

- **Forge.ipa** is the everyday thin application and remains below 500 MB compressed.
- **Forge Seed.ipa** is the same application with a verified Ubuntu recovery payload and remains below 3.8 GB compressed.
- Both artifacts use `com.bitloop.forge`. Seed uses build `2N`; thin uses `2N + 1`, allowing the thin build to replace Seed without changing the data container.

## Repository layout

- `Forge/` — native Swift application.
- `runner/` — standalone FastAPI/Podman LAN runner.
- `images/` and `guest/` — reproducible ARM64 guest definitions and guest support.
- `release/` — signed asset catalog, Seed/thin, feed, and release tooling.
- `bridge/` — accelerator protocol and guest SDKs.
- `runtime/` — pinned UTM source boundary.
- `docs/` — architecture, platform constraints, threat model, and release gates.

## Host requirements

The iPad application builds on Linux using the existing pinned Swift 6.4, Xcode 27 SDK, and xtool toolchain under `~/.local/share/dynalan-ios`. Large image builds require at least 100 GiB free and preserve a 30 GiB host safety floor.

```sh
make test
make ipa-thin
make validate
```

The resulting development artifact is `build/releases/Forge-0.1.0-3-thin.ipa`. Build a Seed artifact only after staging signed `manifests/` and `chunks/`:

```sh
make ipa-seed SEED_ASSETS=/absolute/path/to/staged-seed-assets
```

Forge-authored source is Apache-2.0. Bundled or linked UTM, QEMU, and other third-party components retain their own licenses and source-distribution obligations; see `NOTICE`.
