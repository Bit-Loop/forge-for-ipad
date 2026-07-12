# Forge runner workstation image

The Containerfile is based on the Ubuntu 26.04 multi-platform index digest captured on 2026-07-12. It installs C/C++, Rust, Python, build systems, debuggers, formatters, linters, and package managers used by `ToolchainPlanner`.

Build it on the runner host; the runner never pulls job images implicitly:

```sh
podman pull docker.io/library/ubuntu@sha256:b7f48194d4d8b763a478a621cdc81c27be222ba2206ca3ca6bc42b49685f3d9e
podman build --pull=never -t localhost/forge-workstation:0.1.0 runner/image
systemctl --user edit forge-runner
# Set FORGE_DEFAULT_IMAGE=localhost/forge-workstation:0.1.0
```

Network is disabled per job unless Forge is executing an explicit dependency-fetch phase. Cargo, Conan, vcpkg, uv, pip, and Poetry can therefore install normal project dependencies without giving every build unrestricted egress.

Those package-manager caches live in `/forge-cache`, a per-owner/job tmpfs-backed Podman volume
shared across that job's steps and removed at the job boundary. Its hard size bound defaults to
2 GiB and is configurable with `FORGE_MAX_CACHE_BYTES`. It deliberately does not persist across
jobs: bounded ephemeral caching avoids cross-client data leakage and unbounded host growth until
the runner has a quota-enforced, tenant-partitioned persistent cache.

Before a stable release, rebuild this image in CI, record every installed package version and OCI layer digest in the SBOM, then promote the image by digest rather than tag.
