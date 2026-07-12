# Forge Accelerator Bridge

This directory is the transport-neutral contract and guest SDK for Forge's
Core ML and Metal bridge. The native iPadOS implementation deliberately lives
outside this directory.

Protocol version `1.0` is served only to the active guest at
`http://10.0.2.2:4777/accelerator/v1`. Every request requires a 256-bit,
per-boot bearer token injected into the guest by Forge. Clients reject other
authorities and redirects by default so the token cannot be forwarded to a LAN
service.

## Layout

- `openapi/forge-accelerator-v1.openapi.json` — HTTP operations and security.
- `schema/forge-accelerator-v1.schema.json` — canonical request/response types.
- `python/` — dependency-free Python package and `forge-accelerator` CLI.
- `rust/` — asynchronous Rust client crate.
- `include/` — portable C transport ABI and C++ wrapper.
- `examples/` — minimal guest examples.

## Job lifecycle

Compilation, prediction, and dispatch return `202 Accepted` with a durable job
record. Poll `GET /jobs/{id}/events?after=N` and advance to `next_after` to
resume without duplicating events. A terminal job has state `succeeded`,
`failed`, or `cancelled`. Results use a stable `kind` discriminator.

`DELETE /jobs/{id}` requests cancellation and is idempotent. A cancelled job
may still report cleanup events before reaching its terminal state.

## Scratch objects

Large models and tensors are placed in the shared Forge scratch root by the
guest. API payloads never contain absolute paths: a `ScratchReference` carries
a normalized relative path, exact byte count, and SHA-256. The host resolves
the path beneath its configured scratch root and rechecks the size and digest
before use. `POST /scratch/verify` lets a guest validate an object early. Version
1 transports regular files only. It intentionally has no archive encoding or
host extraction contract; a future version must specify bounded extraction and
link/path handling before `mlpackage` or `mlmodelc` directory transfer can be
advertised safely. Consequently, Core ML compilation in v1 accepts `mlmodel`.

Inline tensor payloads are base64 and are limited by the advertised
`max_inline_bytes`. Callers must honor all limits returned by `capabilities`;
the schema maxima are protocol ceilings, not device promises.

## Authentication and boot changes

The guest agent supplies `FORGE_ACCEL_TOKEN`. Tokens are never accepted in
URLs or CLI arguments. A client should cache the `boot_id` returned by
`capabilities`; a changed boot ID invalidates model/library handles and
requires reacquiring the new token. Errors are structured and include a
request ID suitable for local diagnostics.

## Verification

```sh
make -C bridge test
```

The tests validate the OpenAPI document against the canonical schema, exercise
Python request construction and failure behavior, compile the C and C++
headers, and run the Rust crate tests when Cargo is available.
