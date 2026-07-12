# Forge release tooling

`forge-release` builds and verifies the small everyday Forge IPA and the offline
Seed IPA. It also owns signed runtime-pack manifests, resumable pack
materialization, SideStore source generation, SBOM/NOTICE checks, and recovery
copy manifests.

The tooling is deliberately independent of Xcode and xtool. Build systems hand
it completed artifacts; it emits deterministic metadata or rejects the release.
All JSON written by this package is canonical (sorted keys, UTF-8, no
whitespace, no floating point values).

## Invariants

- Thin build number: `2N + 1`; maximum artifact size: exactly 500,000,000 bytes.
- Seed build number: `2N`; maximum artifact size: exactly 3,800,000,000 bytes.
- Both artifacts must use `com.bitloop.forge` and the same marketing version.
- Runtime packs and recovery manifests are signed with Ed25519.
- A materialized pack is activated only after every file is verified.
- Existing valid newer packs are never replaced by Seed payloads.
- Private key material is forbidden from all release output, including ZIP/IPA
  members.

Run `python -m forge_release --help` after installing this directory, or run
`pytest` for the complete contract test suite.
