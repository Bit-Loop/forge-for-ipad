# Forge guest images

This directory describes, builds, and validates the Linux environments used by
Forge. Image construction is deliberately separate from the iPad build: large
downloads happen only in the image pipeline, and the resulting artifacts enter
the app through a signed asset manifest.

## Environments

| ID | Form | Base | Delivery |
| --- | --- | --- | --- |
| `ubuntu-seed` | QEMU ARM64 VM | Ubuntu 26.04 LTS cloud image | Seed IPA |
| `manjaro-arm` | QEMU ARM64 VM | Manjaro ARM generic EFI | optional pack |
| `archlinuxarm-lxc` | LXC rootfs | Arch Linux ARM generic aarch64 | optional pack inside Ubuntu |

The Ubuntu Seed contains the `core`, `cpp`, `rust`, `python`, `containers`, and
`lxc` packs. Desktop goodies, the full workstation, heavy ML packages, Manjaro,
and Arch Linux ARM are downloadable packs. The exact selection is recorded in
each image manifest.

## Reproducibility contract

1. `sources.lock.toml` identifies upstream bytes. SHA-256 is mandatory when an
   upstream publishes it. Arch Linux ARM uses its detached OpenPGP signature.
   The legacy Manjaro image publishes only SHA-1, so it is never release-ready
   until the release mirror adds a SHA-256 in a generated lock.
2. Package lists are data in `packs/*.toml`, not embedded in provisioning code.
3. Provisioners are idempotent. They write a normalized receipt containing the
   source identity, pack set, package versions, and provisioner revision.
4. `SOURCE_DATE_EPOCH`, locale, timezone, and machine identity are fixed while
   building. First-boot bootstrap regenerates host keys and machine identity.
5. A Forge stable release references immutable promoted artifacts. It never
   resolves a rolling `latest` URL on the iPad.

Run the offline contract suite:

```sh
python3 images/scripts/imagectl.py validate
python3 -m unittest discover -s images/tests -v
python3 -m unittest discover -s guest/tests -v
```

Show a build plan without downloading or changing a disk:

```sh
python3 images/scripts/imagectl.py plan ubuntu-seed
```

Actual image assembly is intentionally explicit and tool-checked:

```sh
images/scripts/build-image.sh ubuntu-seed /srv/forge/sources/ubuntu.img /srv/forge/out
```

The builder verifies the source before invoking `qemu-img`, `virt-customize`,
and the matching provisioner. Release automation must additionally boot the
artifact, wait for `forge-guest-health --json`, seal it, and sign its asset
manifest.
