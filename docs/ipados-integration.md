# iPadOS integration and resilience

Forge targets iPadOS 27 and a free Apple Personal Team. The project deliberately
does not request paid entitlements or claim App Store compatibility. Provision
lifetimes, device limits, and re-signing behavior are controlled by Apple and
the chosen sideloading tool; Forge cannot extend or bypass them.

## Multiple windows and resizing

The application uses a typed `WindowGroup`, so iPadOS can create more than one
Forge scene. Commands and context menus can open independent hub, workspace,
terminal, desktop, debugger, and assistant windows. All windows share the same
in-process application model and stores.

The workspace layout responds continuously to width:

- Below 820 points, it shows the editor and bottom panel.
- At 820 points, it adds the explorer.
- At 1,180 points, it also adds the inspector and uses the wider explorer.

These are layout thresholds, not device assumptions. Stage Manager, Split View,
external-display, rotation, and arbitrary-window testing still needs to happen
on physical iPadOS 27 hardware. Per-window navigation restoration is also not
implemented.

## Files, Textastic, and Working Copy

The application plist enables file sharing and opening documents in place, and
registers as an alternate editor for source code and plain text. Forge-created
projects live at `On My iPad/Forge/Workspaces` as ordinary directories. This is
the current interoperability contract:

1. Create a Forge workspace.
2. Use the Files document provider from Textastic or Working Copy to open or
   clone into that directory.
3. Keep Git ownership in Working Copy and edit the same files from either app.

There is no private Textastic or Working Copy API dependency. Forge has a Files
folder picker, rejects folder symlinks, stores security-scoped bookmarks, and
restores accessible external workspaces on launch. A file URL delivered to the
app selects its registered workspace; an otherwise unscoped file is copied
into a new Files-visible Forge workspace because iPadOS does not extend a
single-file grant to its parent directory.
There is no coordinated external-file access, Git client, direct third-party
callback, stale-buffer detector, or conflict resolver yet. Concurrent edits
from another app can therefore overwrite an open Forge buffer.

## Deep links

`ForgeCore` validates these `forge://` routes:

| Route | Parser | Current app effect |
| --- | --- | --- |
| `forge://open?workspace=W&file=F&line=L&column=C` | Implemented | Selects a matching workspace; file and position are ignored. |
| `forge://run?workspace=W&task=T` | Implemented | Adds a local queued job record; it does not execute the task. |
| `forge://terminal?workspace=W` | Implemented | Selects a matching workspace; it does not open a terminal window. |
| `forge://artifact?digest=<sha256>` | Implemented | Displays the validated digest in an alert. |
| `forge://runner/pair?endpoint=URL` | Implemented | Displays the HTTP(S) endpoint; it does not pair. |

Deep links are convenience inputs, not authorization. A future implementation
must require confirmation for pairing, execution, and external data access.

## Foreground, background, suspension, and process death

iPadOS owns process scheduling. Forge cannot promise indefinite background CPU,
network, VM, or JIT availability. The current code registers:

- `com.bitloop.forge.processing` as a `BGProcessingTask` identifier; its handler
  currently completes immediately.
- `com.bitloop.forge.continued.*` for iPadOS continued-processing tasks. The
  coordinator can submit a progress-bearing operation with queue strategy.

Continued-operation closures are held only in memory. If the app process dies,
the relaunched handler cannot recover the closure itself. Before remote
submission, Forge persists the exact request and deterministic idempotency key;
after attachment it persists the remote job ID and last consumed event
sequence. The runner continues independently and Bonjour discovery reconciles
either boundary on a later launch.

Durability currently comes from small, synchronous state boundaries:

- Source files are atomically replaced after each editor change.
- Edit deltas are appended and synchronized to a journal before the save task
  completes.
- Local job records are atomically written as protected JSON.
- Pending runner requests are persisted before the network submission boundary.
- Remote runner references and SSE cursors are persisted with those records.
- Runner job requests, states, and events live in SQLite on the runner host.
- Seed installation uses an embedded public key, verified chunk prefixes,
  safe pack identity grammar, exact file inventories, complete file/tree
  hashes, versioned staging, and final activation. The
  release library implements the matching authoring and verification contract.
- The guest `checkpoint` method flushes guest filesystems; it does not snapshot
  VM memory or CPU state.

On normal iPad suspension, already-written files remain available. After a
crash or termination, Forge reloads workspace listings and local jobs. It does
not replay edit journals, restore editor selections, resume a remote pack
download, or restore a VM. It does reconnect persisted runner jobs. An
interrupted embedded Seed materialization can reuse verified partial chunks
when bootstrap retries it. Runner jobs can continue independently of the iPad;
a runner restart requeues interrupted jobs from the beginning and provides a
recovery event.

## JIT and native execution

iPadOS does not grant an ordinary sideloaded application unrestricted executable
memory or arbitrary native-code loading. Forge includes a URL handoff for
StikDebug and a BRK `0x69` adapter intended for UTM's documented JIT handshake,
but it has not been exercised on the target device and UTM is not integrated.
Without a valid JIT path, a future QEMU integration would have to use a slower
interpreter where licensing and platform rules permit it.

C, C++, Rust, and Python execution is not currently available in an on-device
guest. The workspace scene can pair with Forge Runner, detect toolchains,
snapshot source, and submit sandboxed fetch/lint/build/test/run commands to a
separate host. Native job output, PTY, and artifact UI remain unfinished.
