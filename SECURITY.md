# Security policy

Forge has not reached a stable release. Security fixes are accepted for the
current default branch; no historical version currently receives a guaranteed
support window.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository. If that
feature is unavailable, contact the repository owner privately through the
verified address on the owner's GitHub profile. Do not open a public issue or
attach workspace contents, tokens, signing material, or a weaponized exploit.

Include the affected commit, component, preconditions, impact, minimal
reproduction, and any suggested mitigation. Reports involving arbitrary source
execution should distinguish expected code execution inside a declared guest
or rootless container from an escape into the iPad app, runner host, another
client's data, or the release-signing boundary.

The maintainer will acknowledge a report when available, validate it, agree on
a disclosure timeline, and credit the reporter if requested. There is no paid
bug-bounty program.

## High-priority boundaries

- Release signature or asset-verification bypass.
- Exposure of signing keys, runner tokens, guest tokens, or workspace source.
- Path traversal or symlink escape during snapshot, pack, Seed, recovery, or
  scratch-file handling.
- Authentication bypass or cross-client access in Forge Runner.
- Rootless Podman, future VM, JIT, guest-agent, or accelerator host escape.
- Silent data loss across editing, background expiration, or recovery.

Read [the threat model](docs/threat-model.md) before assessing behavior. The
[known limitations](docs/known-limitations.md) are not automatically security
vulnerabilities, but a bypass of an explicitly stated safety boundary may be.
