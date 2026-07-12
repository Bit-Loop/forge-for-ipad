# Thin, Seed, assets, and updates

Forge is designed around one application identity and two delivery artifacts:

- **Thin** is the everyday, frequently updated IPA with no embedded runtime.
  Its compressed size budget is exactly 500,000,000 bytes.
- **Seed** is a resignable copy of the same app with recovery bytes under
  `Payload/Forge.app/SeedAssets`. Its compressed size budget is exactly
  3,800,000,000 bytes.

Both variants use `com.bitloop.forge` and the same marketing version. For
release sequence `N`, release tooling assigns Seed build `2N` and thin build
`2N + 1`. Installing the newer thin build is intended to replace Seed while
leaving the application data container intact. Actual retention depends on the
sideloading tool preserving the same application identity and not uninstalling
the app first; the repository cannot guarantee it.

## What the tooling does today

The `forge-release` package can:

- Generate and verify canonical Ed25519-signed, content-addressed pack
  manifests.
- Split files into reusable SHA-256 chunks and verify every declared byte.
- Resume materialization from the longest verified chunk prefix, stage a full
  pack, and atomically activate it only after tree verification.
- Preserve an already active, valid newer pack when older Seed content appears.
- Assemble Seed from a thin IPA, remove the old signature/provision, write the
  Seed variant/build fields, and add deterministic Seed assets.
- Generate package metadata, SideStore source data, signed recovery
  inventories, SPDX/NOTICE checks, and private-key leakage checks.

Seed assembly intentionally produces an unsigned/resignable IPA. It must be
signed by the user's sideloading path after assembly.

## Native Seed installation and remaining update work

On bootstrap, a Seed build looks for embedded manifests and chunks. The native
`AssetManager` verifies canonical Ed25519-signed manifests with the checked-in
public key, validates path and chunk inventories, resumes each partial file from
its verified prefix, verifies the complete staging tree, activates the version,
and preserves a valid newer installed pack. A thin build has no `SeedAssets`
directory and skips this work.

The app does not yet retrieve a remote signed catalog, download missing chunks,
garbage-collect old packs, expose update progress, or roll back after a runtime
health failure. No actual Seed runtime payload or promoted signed catalog is
included in this repository.

The thin build script validates bundle identity, variant, size, multiwindow
metadata, and private-key absence. Project validation requires an odd build
number (currently build 3). The Seed assembler writes build 2 for release
sequence 1 and changes the bundle variant while removing stale signing data.
End-to-end release orchestration must still validate both package metadata
records together before publishing.

## Intended update transaction

The embedded Seed path implements the verification, staging, and activation
portion of this transaction. A downloadable update should follow this order:

1. Fetch signed catalog metadata over an authenticated channel.
2. Verify the catalog signature with a pinned release public key.
3. Reject incompatible runtime ABI, architecture, or downgrade requests.
4. Download missing chunks to a content-addressed cache using partial files.
5. Verify chunk, file, and complete manifest hashes.
6. Materialize into a versioned staging directory and atomically activate it.
7. Retain the prior active version until the new runtime passes a boot health
   check. This rollback/health gate is not implemented yet.
8. Reclaim only unreferenced, regenerable content under the configured quota.

Workspaces, journals, credentials, and explicit external recovery copies must
never be treated as cache. Native Seed import uses the same signed pack model
and does not replace a valid newer active pack.

## Offline recovery

A recovery copy should contain the Seed IPA, thin IPA where available, public
verification key, signed recovery inventory, SBOM, notices, and corresponding
source offers required by bundled licenses. `forge-release` can inventory and
sign these files, but creating, copying, and testing the complete recovery set
is still a release-operator responsibility.

## UTM, QEMU, and licenses

Forge-authored source is Apache-2.0. UTM, QEMU, Linux distributions, packages,
firmware, and linked libraries retain their own licenses. `runtime/` pins the
intended UTM source revision and provides an explicit recursive fetch, but that
code is not presently linked or vendored. A future binary that incorporates it
must be built from reviewed, pinned source and ship all required notices,
license text, source offers or corresponding source, relink materials where
applicable, and modification records. A generated SBOM is a release gate, not
a substitute for license-specific obligations.

No stable Seed or thin binary should be published until the checklist in
[development and release verification](development.md) passes.
