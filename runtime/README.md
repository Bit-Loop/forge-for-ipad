# UTM/QEMU runtime boundary

Forge pins UTM v4.7.5 (`048ca7498ea3a374439149d51739d94c5300bcda`), the release that includes QEMU 10.0.2 and iOS 26 StikDebug support. `fetch-utm.sh` deliberately is not run by ordinary builds: its recursive dependency checkout is large and release builds must inventory the resulting LGPL/GPL components.

Two runtime products are planned from the same pin:

- `iOS` supplies QEMU TCG with JIT when StikDebug has enabled the signed Forge process.
- `iOS-SE` supplies the TCI interpreter fallback when JIT is unavailable.

Neither product may claim Hypervisor.framework under ordinary sideloading. UTM’s own support table limits the sideloaded IPA to JIT without Hypervisor; the hypervisor build requires TrollStore/private entitlement conditions. Forge therefore reports this capability at runtime and never infers it from the M5 hardware alone.

The checked-in Swift target intentionally compiles without UTM binaries. A promoted runtime build must:

1. Fetch the exact recursive source pin.
2. Build both iPhoneOS arm64 schemes using UTM’s `scripts/build_utm.sh` flow.
3. Export the QEMU/UTM libraries and headers as a versioned, signed Forge asset pack.
4. Generate SPDX and NOTICE data plus corresponding-source references.
5. Run the physical-device boot, JIT, save-state, low-memory, and background-expiration gates in `docs/verification.md`.

This separation keeps the 1.5 MB thin application independently buildable while preventing an unverified prebuilt emulator blob from entering source control.
