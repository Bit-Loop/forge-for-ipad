# Development and release verification

## Local test paths

The native app targets iPadOS 27. The checked-in Linux build path expects the
pinned DYNALAN Swift/Xcode SDK/xtool environment under
`~/.local/share/dynalan-ios`. Host-only `ForgeCore` tests do not import SwiftUI
or an Apple SDK:

```sh
FORGE_CORE_ONLY=1 swift test
```

The repository's `make test-swift` target uses the pinned Swift binary and its
compatibility libraries. Build the current thin development IPA with:

```sh
make ipa-thin
python3 tools/validate_ipa.py build/releases/Forge-0.1.0-3-thin.ipa
python3 tools/validate_project.py
```

The IPA is not a stable release, is not signed for another person's device,
and does not contain a working compiler runtime.

Test the independently installable components without building guest images:

```sh
python3.13 -m venv runner/.venv
runner/.venv/bin/pip install -e 'runner[test]'
runner/.venv/bin/ruff check runner/src runner/tests
(cd runner && .venv/bin/python -m mypy src)
runner/.venv/bin/pytest runner/tests

python3.13 -m venv release/.venv
release/.venv/bin/pip install -e 'release[test]'
release/.venv/bin/pytest release/tests

python3 images/scripts/imagectl.py validate
python3 -m unittest discover -s images/tests -v
python3 -m unittest discover -s guest/tests -v

make -C bridge test
make -C bridge format-check
```

Image contract tests read manifests and small fixtures only. Actual image
construction requires large downloads, QEMU/libguestfs tools, generous free
space, and an explicit output directory. It is intentionally excluded from CI.

## Continuous integration

`.github/workflows/ci.yml` runs six independent checks:

- ForgeCore on Linux Swift 6.2 with the iOS application target omitted.
- Runner unit/integration-contract tests plus Ruff and strict mypy on sources.
- Release tests.
- Image and guest offline contract tests.
- Accelerator Python/C/C++ contracts, Rust formatting, and locked Rust tests.
- Markdown links/style, workflow syntax, ShellCheck, and shell parser checks.

CI has read-only repository permission, uses no signing or sideloading secrets,
does not assemble an IPA, does not start Podman, and does not download or boot
multi-gigabyte runtime images. Passing CI therefore does not prove the iPad app
compiles against the pinned Xcode 27 SDK or works on hardware.

## Release gates

Before marking a thin/Seed pair stable, a release operator must record evidence
for every gate below.

1. All CI checks pass from the exact release commit and the worktree is clean.
2. The native app compiles with the pinned, recorded Swift/Xcode 27/xtool
   versions; strict-concurrency warnings remain errors.
3. Thin and Seed contain the same reviewed application binary and marketing
   version, use `com.bitloop.forge`, carry paired `2N + 1`/`2N` build numbers,
   and pass their exact compressed-size budgets.
4. Neither IPA, feed, recovery set, log, nor source archive contains a private
   signing key, provisioning secret, runner token, or user workspace.
5. Every Seed/downloadable input is immutable, release-eligible, signature or
   SHA-256 verified, license-audited, and represented in the SBOM and notices.
   The current Manjaro source lock fails this gate because upstream supplies
   only SHA-1.
6. The Seed guest boots from a clean install, reports required health
   capabilities, compiles and runs C/C++/Rust/Python smoke projects, installs
   packages, and survives a shutdown/reboot. This path is not automated yet.
7. Thin installed over Seed preserves workspaces and valid newer packs. Seed
   installed into a clean container restores the embedded pack. Interrupted
   embedded materialization resumes without activating partial content. Native
   Seed installation exists, but no real payload or physical-device evidence
   exists, so this gate currently cannot pass.
8. Multiple windows, all resizing classes, Files/Open In, Textastic, Working
   Copy, external keyboard/pointer, VoiceOver, memory pressure, background
   expiration, process termination, and storage exhaustion are tested on the
   target iPadOS 27 device. Known device failures block stable release.
9. StikDebug/JIT is tested from a fresh free-signing install. Interpreter mode
   is tested independently. Neither path exists end to end yet.
10. Runner pairing, restart recovery, cancellation, artifact retrieval, network
    policy, and a real rootless Podman build are exercised on the deployment
    host. Contract tests use a fake executor for determinism.
11. The accelerator bridge is tested against the native server and a real Core
    ML/Metal backend with malformed paths, oversized tensors, token rotation,
    guest reboot, timeout, cancellation, and memory pressure. The
    transport/validation server exists; its execution backend and app lifecycle
    wiring are not implemented yet.
12. Seed, thin, public key, feed, SBOM, notices, corresponding-source material,
    and recovery inventory are copied to offline media and verified from that
    copy before publication.

## Current release blockers

The repository is an engineering foundation, not the requested complete iPad
workstation. The blocking gaps are tracked explicitly in
[known limitations](known-limitations.md). A green unit-test run must not be
used to relabel contracts or placeholder scenes as working device features.
