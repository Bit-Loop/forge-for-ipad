# Known limitations

This list is intentionally blunt. It prevents prototype UI and tested protocol
contracts from being mistaken for a finished iPad development workstation.

## Native application

- The editor is a basic `TextEditor`; there is no Runestone/tree-sitter, syntax
  highlighting, LSP client, formatter, lint pipeline, completion, or real Vim
  mode.
- The terminal surface is static, and the desktop, debugger, and assistant are
  placeholders.
- Workspace navigation recursively indexes regular files and excludes Git
  internals, but rename, delete, search, and Git status are absent.
- External Files/Open In declarations, a folder picker, and persistent
  security-scoped bookmarks exist, but coordinated access, conflict handling,
  stale-buffer detection, and direct Textastic/Working Copy actions do not.
- Deep-link parsing is broader than deep-link execution. File positions,
  terminal windows, runner pairing, and artifact retrieval are not wired.
- Runner jobs are integrated through discovery, Keychain pairing, snapshot
  upload, task submission, resumable events, and persisted remote references.
  Output rendering, cancellation UI, and artifact retrieval UI remain absent.

## Execution and packages

- UTM source is pinned behind an explicit fetch boundary, but UTM and QEMU are
  not linked, built, or controlled by the app. No Linux guest boots on iPad.
- WAMR/WASI is named in models but is not integrated.
- C, C++, Rust, and Python task plans can execute through a paired LAN runner.
  They are still unavailable on-device until the UTM runtime is integrated,
  and the runner workstation image has not been exercised with real Podman on
  this development host.
- Image definitions and provisioners have not produced a promoted, boot-tested
  release artifact. Manjaro is explicitly non-release-eligible under the
  current SHA-1-only upstream lock.
- The runner workflow is integrated, but its production Podman PTY adapter is
  absent; the WebSocket PTY contract is tested with an echo test executor.

## Durability and background work

- User-triggered runner work submits through continued processing. Process
  death loses the operation closure, but Forge persists either the exact
  idempotent runner request or the attached runner job ID and last event
  sequence; a later discovery reconciles submission or resumes observation.
- Edit journals and atomic saves are ordered, and Forge rejects a save when
  another Forge window changed the loaded revision. Journals are not replayed
  or compacted, and external Files-provider coordination is not implemented.
- Runtime state is in memory. There is no VM memory snapshot, crash recovery,
  suspend/resume controller, or background runtime policy.
- Guest checkpoint flushes filesystems only. Runner restart recovery requeues a
  command from the beginning and can repeat side effects.

## Distribution and hardware

- Native bootstrap can verify, resume, materialize, and activate embedded Seed
  packs while preserving a valid newer pack. The app does not retrieve a
  catalog, download remote chunks, report progress, roll back on failed runtime
  health, or evict packs.
- Pack downgrade protection currently uses numeric string ordering. Release
  operators must use monotonically increasing numeric versions; semantic
  prerelease ordering is not implemented.
- No Seed IPA with a real guest payload has been built. The thin development IPA
  is not an end-user release; its odd build number is validated and the Seed
  assembler writes the paired preceding even build number.
- The StikDebug URL/JIT adapter is unverified on the target iPad, and no
  interpreter fallback is integrated.
- Core ML/Metal protocol, guest SDKs, and a native authenticated transport with
  safe scratch verification exist. No Core ML/Metal execution backend is
  attached or launched. Neural Engine execution, bare-metal access, and
  accelerator passthrough are not available.
- Free-signing renewal, SideStore update behavior, same-container replacement,
  iPadOS 27 background scheduling, memory pressure, and 13-inch M5 performance
  have not been validated on physical hardware.

These are stop-ship limitations for any claim that Forge can already compile
or run arbitrary C, C++, Rust, or Python projects on an iPad.
