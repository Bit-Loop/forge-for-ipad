# Contributing to Forge

Forge is early-stage and its most important convention is honesty: distinguish
implemented device behavior from a tested contract, a prototype surface, and a
planned integration. Update [the architecture status](docs/architecture.md) and
[known limitations](docs/known-limitations.md) when that boundary changes.

## Before opening a change

1. Keep Forge-authored code Apache-2.0 compatible and record every new bundled
   or linked component in `NOTICE` plus the future SBOM inputs.
2. Do not commit signing identities, provisioning profiles, Ed25519 private
   keys, pairing tokens, workspace data, runtime images, build caches, or
   generated package artifacts.
3. Preserve the bundle identifier, thin/Seed size limits, paired build policy,
   safe-relative-path checks, and offline image-build boundary.
4. Add tests at the narrowest stable layer. Device behavior also needs a
   written physical-device test result; a host test is not a substitute.
5. Keep untrusted source and guest data outside the native host trust boundary.

## Verification

Run the checks relevant to the component you changed. The canonical command
matrix and release gates are in
[development and release verification](docs/development.md). At minimum, run:

```sh
FORGE_CORE_ONLY=1 swift test
runner/.venv/bin/pytest runner/tests
python3 -m pytest release/tests
python3 images/scripts/imagectl.py validate
python3 -m unittest discover -s images/tests -v
python3 -m unittest discover -s guest/tests -v
make -C bridge test
```

For native UI changes, also build the IPA with the pinned SDK, exercise every
responsive width around 820 and 1,180 points, and test keyboard and VoiceOver
navigation on iPad. Explain any command you could not run.

## Pull requests

Keep changes scoped and reviewable. Describe the user-visible outcome, trust or
licensing impact, test evidence, and remaining limitations. Do not include a
stable binary release in a pull request. Security-sensitive findings should use
the private process in [SECURITY.md](SECURITY.md), not a public issue.
