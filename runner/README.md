# Forge Runner

Forge Runner is the durable LAN execution service used by Forge for iPad. It accepts
content-addressed source snapshots, runs jobs in rootless Podman sandboxes, persists event
history in SQLite, and exposes resumable logs and artifacts through `/forge/v1`.

## Development

```sh
python3.13 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Start a development server with an explicit one-time pairing code:

```sh
FORGE_PAIRING_CODE=123456 .venv/bin/forge-runner --data-dir ~/.local/share/forge-runner
```

Pair once with `POST /forge/v1/pair`, then send the returned token as
`Authorization: Bearer <token>`. The token is shown only once and is stored as a SHA-256
digest. If no pairing code is supplied, the runner creates one and logs it at startup.
When a paired runner's address changes, Forge requires a fresh one-time pairing code. The
runner rotates the bearer under the existing random owner ID, so durable jobs and idempotency
keys remain in the same authorization scope without presenting the old bearer to the new URL.
The runner also creates `instance-id` in its data directory. This canonical UUID persists
across address changes and is returned by authenticated capabilities; startup fails closed if
the identity file is malformed instead of silently replacing the installation's identity.

Production jobs require rootless Podman and either a validated request image or a configured
default image. The runner never pulls implicitly. `FakeExecutor` exists only for deterministic
tests and local contract development.

Podman execution is native-architecture only: `aarch64` is canonicalized to `arm64`,
`x86_64` to `amd64`, and a request that does not match the runner host is rejected. Dependency
caches are shared only between steps of one job. Each job gets an owner/job-hashed Podman volume
backed by a size-bounded `tmpfs`; the default bound is 2 GiB and
`FORGE_MAX_CACHE_BYTES` configures it. The runner rejects non-positive bounds, force-removes stale
state before creation, and removes the volume after success, failure, timeout, or cancellation.
This bounded ephemeral cache is the safe fallback until a separately quota-enforced,
tenant-partitioned persistent cache exists.

Durable storage has independent aggregate ceilings in addition to per-object limits:

- `FORGE_MAX_CAS_BYTES` bounds physical CAS bytes (128 GiB by default). Reservations cover
  concurrent writes, are reconstructed from disk after restart, and include artifact blobs.
- `FORGE_MAX_ARTIFACT_STORAGE_BYTES`, `FORGE_MAX_ARTIFACT_COUNT`, and
  `FORGE_MAX_ARTIFACT_METADATA_BYTES` bound durable artifact references globally. Per-job bytes
  and count are separately bounded by `FORGE_MAX_JOB_ARTIFACTS_BYTES` and
  `FORGE_MAX_JOB_ARTIFACT_COUNT`.
- `FORGE_MAX_EVENT_STORAGE_BYTES` and `FORGE_MAX_EVENT_COUNT` bound durable replay events. Once
  exhausted, additional replay events are omitted while the authoritative job state continues
  to update.
- `FORGE_MAX_DATABASE_BYTES` sets SQLite's hard page ceiling (4 GiB by default); WAL
  autocheckpointing and its journal-size limit bound transient journal retention.

The CAS reservation ledger assumes the supplied single-process systemd/CLI deployment. Do not
start multiple Forge Runner processes over the same data directory.

## Installation

`deploy/install.sh` installs an isolated virtual environment and user systemd unit, prints the
stable instance UUID, and renders a per-installation Avahi service to
`~/.config/forge-runner/forge-runner-avahi.service`. Install that rendered file using the exact
command printed by the installer to advertise `_forge-runner._tcp`. The checked-in Avahi file is
a template: its `instance_id` TXT record must be rendered by the installer and must not be copied
directly.
