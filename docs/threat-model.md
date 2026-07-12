# Threat model

Forge is a developer tool that intentionally handles untrusted source code and
eventually intends to execute it. The security boundary is the execution
sandbox, not the source repository. This model covers the code currently in the
repository; a future native VM and accelerator execution backend require a new
review.

## Assets and trust boundaries

Protected assets include workspace source, runner bearer tokens, release
signing keys, downloaded runtime bytes, build artifacts, guest tokens, and the
iPad application data container.

The main boundaries are:

- iPad app container to Files providers and external editors.
- iPad app to a local-network Forge Runner.
- Runner API to a rootless Podman container.
- Future iPad host to an untrusted Linux guest.
- Guest scratch files to the native accelerator transport and its future Core
  ML/Metal backend.
- Release operator and offline signing key to public catalogs and IPAs.

Forge assumes the iPad OS and runner host OS are trusted and updated. Source,
project manifests, compiler input, guest packages, downloaded assets, LAN
traffic, and container output are untrusted.

## Implemented controls and remaining exposure

| Threat | Current control | Residual risk or missing work |
| --- | --- | --- |
| Corrupt runtime/update | SHA-256 file/chunk/tree verification and Ed25519-signed canonical manifests in release tooling; native Seed install pins a public key, verifies/resumes chunks, stages, and activates only a verified tree. | Remote catalog/download, runtime health rollback, key rotation, and reproducible-release controls are not implemented. Compromise of the release-signing process remains critical. |
| Path traversal | Core revision paths, runner manifests/globs, release packs, Seed members, recovery inventories, and bridge scratch references reject unsafe relative paths. External workspace registration rejects directory symlinks. | Future archive extractors and guest shared folders need separate review; a bookmarked provider can still change content after registration. |
| Pairing-code guessing | Runner pairing code is one-use, expires, and has an in-memory per-client rate limiter. Tokens are random and stored as a digest. Address migration requires a new code and rotates the bearer without changing the owner scope. | HTTP is allowed, there is no built-in TLS, proxy-aware identity, persistent rate limiter, or remote revocation UI. A token pepper is optional and empty by default. Pair only on a trusted LAN or place the service behind authenticated TLS. |
| Cross-client runner access | Every protected job, event, PTY, cancellation, and artifact lookup is scoped to the owning token and foreign identifiers return not found. Per-job dependency cache volumes are owner/job-derived, bounded, and destroyed at completion. | Paired clients still share the runner host, OCI image, and aggregate service quotas. Kernel, Podman, and service-level denial-of-service remain trust boundaries. |
| Host escape from builds | Production runner requires non-root Podman, never pulls implicitly, uses a read-only root, drops capabilities, enables no-new-privileges, limits CPU/memory/PIDs/time, mounts only a copied workspace, and defaults network off. Native dependency pipelines grant egress only to leading Fetch containers; later build/test/run containers use no network. | Podman, kernel, image, compiler, and fetched-dependency vulnerabilities remain. The copied workspace is writable. Do not run the service as root or expose its API directly to an untrusted network. |
| Job/service interruption | Runner stores intent and events in SQLite, provides idempotency and event replay, and requeues a running job on restart. | Recovery reruns the command from its initial snapshot; it is not process checkpointing and can repeat external side effects. Commands must be idempotent or use explicit external transaction keys. |
| Guest-to-host compromise | Guest-agent methods are restricted and token-authenticated; planned execution uses SSH. Accelerator clients restrict authority/redirects, and the native server uses a per-boot token plus `openat`/`O_NOFOLLOW`, size/digest checks, aggregate limits, and a private read-only staged copy before backend delegation. | The server is not launched and has no Core ML/Metal backend; VM sandbox, shared filesystem, and JIT isolation remain unvalidated. Its loopback/QEMU reachability needs physical testing. |
| Secrets in artifacts | Release validation rejects known private-key patterns, runner secret references fail closed without a provider, and paired runner credentials use a this-device-only Keychain record. | Pattern checks are not secret scanning. Pairing UI/lifecycle is absent, and output/source can still contain user secrets. |
| External editor conflict | Atomic source replacement, security-scoped folder bookmarks, an append-only edit journal, and content-revision checks prevent silent stale writes between Forge windows. | No Files-provider coordination, version merge, or journal replay exists; an external writer can still race the final check/write boundary. |
| Deep-link abuse | Routes validate scheme, required fields, digest syntax, and HTTP(S) pairing endpoints. | Current effects are limited, but future run/pair/import actions must require user confirmation and must not trust link-supplied paths. |

## Release-key policy

An Ed25519 private release key must never be committed, bundled in an IPA,
placed in CI, or stored on the iPad. CI may validate unsigned fixtures and public
keys only. Stable catalogs and recovery inventories should be signed offline or
through a separately controlled signing system. Key rotation needs an
application update that trusts both the outgoing and incoming public keys for a
bounded transition.

## Security invariants for future runtime work

- A guest is never trusted because it was shipped in Seed.
- Arbitrary guest commands travel over authenticated SSH, not the lifecycle
  agent or accelerator bridge.
- Host services bind only to the private guest transport and authenticate every
  request with a per-boot token.
- Scratch references are relative, size-bounded, digest-bound, and opened
  beneath one configured root without following attacker-controlled links.
- JIT availability changes performance, not validation or authorization.
- Background expiration always ends at a persisted boundary; it is not treated
  as successful completion.
- The runner never pulls an image as a side effect of an untrusted job request.

Report suspected vulnerabilities using the private process in
[SECURITY.md](../SECURITY.md).
