# Architecture and implementation status

Forge is split into a native iPad application, execution services, reproducible
guest definitions, and release tooling. This document describes the repository
as it exists today. “Contract” means a tested protocol or artifact definition;
individual status rows say whether the native application invokes it.

```text
                    signed catalogs and packs
             +-----------------------------------+
             |                                   v
+--------------------------+              +---------------+
| Native iPad application  |<-- Files --->| app container |
| SwiftUI + ForgeCore       |              +---------------+
+------------+-------------+
             |
             +-- planned SSH/guest-agent --> ARM64 Linux guest
             |
             +-- paired CAS/jobs/events ----> LAN Runner
             |
             +-- server boundary, no backend -> Core ML / Metal
```

## Component status

| Area | Status | What exists |
| --- | --- | --- |
| `ForgeCore/` | Implemented and host-tested | Job transitions, scene values, deep-link parsing, project-manifest types, and safe workspace revisions. |
| `Forge/` | Native shell and service clients implemented | SwiftUI scenes, responsive panes, Files-visible and bookmarked external workspaces, atomic source writes, edit journal, local job records, native Seed pack installation, runner discovery/client/Keychain types, a guest-only accelerator server boundary, background-task registration, and a StikDebug handoff. |
| Editor and terminal | Prototype UI | The editor is `TextEditor`; syntax trees, LSP, lint presentation, Vim semantics, and a real PTY renderer are not integrated. |
| Local Linux runtime | Definitions only | Runtime state models, guest/image definitions, bootstrap tools, health checks, and a restricted guest-agent contract exist. UTM/QEMU is not linked and the app cannot boot a guest. |
| `runner/` | Service and native workflow implemented | FastAPI service with one-time pairing, bearer authentication, content-addressed snapshots, SQLite jobs/events, Server-Sent Event replay, artifacts, and rootless Podman execution. The iPad UI discovers/pairs runners and submits detected toolchain commands through content-addressed workspace snapshots. |
| `release/` | Tooling implemented and tested | Canonical JSON, Ed25519-signed packs, resumable materialization, thin/Seed policies, SideStore metadata, recovery inventories, and compliance validation. It is not wired into an end-to-end release job. |
| `images/` and `guest/` | Contracts and scripts implemented | Locked upstream inputs, declarative package packs, provisioning scripts, guest health/agent/pack tools, and offline contract tests. No promoted VM image is stored here. |
| `runtime/` | Source boundary implemented | A UTM commit and version are pinned with an explicit recursive fetch script. No UTM/QEMU binary is checked in or linked. |
| `bridge/` | Contract and guest SDKs implemented | OpenAPI/JSON Schema plus Python, Rust, C, and C++ guest clients. The app contains an authenticated HTTP server, scratch verifier, and backend boundary; it has no Core ML/Metal execution backend and is not started by the app model. |
| AI/NPU | Not implemented | The assistant view is a placeholder. There is no Foundation Models, MLX, Core ML, or Neural Engine execution path in the app. |

## Native application

`ForgeApp` owns one shared `ForgeAppModel` and presents typed SwiftUI windows
using `WindowGroup(for: ForgeScene.self)`. Scene values identify hub, workspace,
terminal, desktop, debugger, and assistant windows. Only the hub and basic
workspace editor are functional; the remaining scenes are explicit empty
states.

Application state is divided by responsibility:

- `WorkspaceCoordinator` creates and enumerates
  `Documents/Workspaces/<name>`, writes starter project files, retains
  security-scoped bookmarks for external Files folders, and appends edit
  journal records under Application Support.
- `JobStore` persists the local job model as JSON and enforces the transition
  graph in `ForgeCore`. Exact pending runner requests are persisted separately
  until idempotent remote submission is attached to a local job.
- `AssetManager` verifies an embedded Ed25519-signed manifest and its chunks,
  resumes partial files from a verified prefix, materializes a staging tree,
  atomically activates Seed packs, and preserves a valid newer active version.
  It does not fetch a catalog or download remote chunks.
- `RunnerDiscovery`, `RunnerClient`, and `RunnerCredentialVault` implement
  Bonjour resolution, endpoint policy, pairing, content-addressed requests,
  resumable event streams, checked artifact downloads, and device-only Keychain
  storage. Settings exposes discovery/pairing, while workspace task controls
  submit snapshots and reconnect to durable remote jobs.
- `AcceleratorBridgeServer` implements per-boot authentication, bounded HTTP
  parsing, no-follow scratch verification, private read-only staging before
  delegation, advertised resource limits, capabilities, and an injectable
  backend boundary. An accepted asynchronous backend must retain the supplied
  lease until its job becomes terminal; that lease owns staged files and
  capacity reservations. No Core ML or Metal backend is attached or launched.
- `RuntimeManager` currently maintains an in-memory runtime snapshot. It is not
  a hypervisor or emulator controller.
- `BackgroundExecutionCoordinator` registers iPadOS processing and continued
  processing handlers. User-triggered runner tasks submit through it; their
  remote job ID and event cursor persist independently of the in-memory handler.
- `StikDebugCoordinator` constructs a `stikjit://` request containing the
  Forge-authored BRK `0x69` adapter. Success has not been verified on a device.

## Workspace and edit data

New workspaces contain `README.md` and `.forge/project.toml`. The project
manifest model supports an execution target, named commands, forwarded ports,
artifact globs, and sync exclusions, but the UI currently writes only the
schema, project name, and Ubuntu runtime fields.

Each source change computes one UTF-16 replacement delta. Forge appends that
delta to `Application Support/Forge/Journals/<workspace-id>/operations.jsonl`,
flushes the file handle, and atomically replaces the edited source file. The
journal is evidence for future replay/recovery work. A content revision check
prevents two Forge windows from silently overwriting each other. The
application does not currently replay or compact the journal, coordinate with
external writers, or merge simultaneous external edits. In-app save tasks are
chained so journal and atomic-file commits retain edit order.

## Execution boundaries

The intended execution choices have different trust and durability properties:

1. A future ARM64 Linux guest would provide C, C++, Rust, Python, packages,
   SSH, PTY, and optional desktop tools. The current guest agent deliberately
   exposes only health, lifecycle, capability, and checkpoint operations;
   arbitrary execution belongs on SSH.
2. Forge Runner executes uploaded snapshots in local, explicitly named,
   rootless Podman images. It persists job intent and events, then requeues an
   interrupted job after service restart. The app hashes/uploads a safe
   workspace snapshot, submits structured argv, persists the remote reference,
   and resumes SSE after the last stored sequence. The runner does not
   checkpoint a running process or container.
3. The accelerator bridge is a guest-to-host contract with a native transport
   and validation boundary. It authenticates and validates scratch-file paths,
   sizes, digests, tokens, and advertised limits before delegation. Actual Core
   ML compilation/prediction and Metal library/dispatch work still requires an
   execution backend.

See [iPadOS integration](ipados-integration.md),
[distribution](distribution.md), and [the threat model](threat-model.md) for
the corresponding platform, release, and security boundaries.
