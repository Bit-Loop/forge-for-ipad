PYTHON ?= python3
TOOLCHAIN_ROOT ?= $(HOME)/.local/share/dynalan-ios
XTOOL ?= $(TOOLCHAIN_ROOT)/bin/xtool
SWIFT ?= $(TOOLCHAIN_ROOT)/swift-current/usr/bin/swift
SWIFT_ENV = LD_LIBRARY_PATH='$(TOOLCHAIN_ROOT)/compat/lib:$(TOOLCHAIN_ROOT)/ldc-1.34.0/lib'

.PHONY: test test-swift test-runner test-release test-images test-bridge ipa-thin ipa-seed validate

test: test-swift test-runner test-release test-images test-bridge

test-swift:
	FORGE_CORE_ONLY=1 $(SWIFT_ENV) $(SWIFT) test

test-runner:
	$(MAKE) -C runner test

test-release:
	$(MAKE) -C release test

test-images:
	sh images/scripts/validate-offline.sh

test-bridge:
	$(MAKE) -C bridge test

ipa-thin:
	DYNALAN_IOS_TOOLCHAIN_ROOT='$(TOOLCHAIN_ROOT)' tools/build_ipa.sh --variant thin

ipa-seed: ipa-thin
	@test -n "$(SEED_ASSETS)" || { echo "SEED_ASSETS must name a staged manifests/ and chunks/ tree" >&2; exit 2; }
	$(MAKE) -C release bootstrap
	release/.venv/bin/forge-release assemble-seed \
		--thin-ipa build/releases/Forge-0.1.0-3-thin.ipa \
		--seed-assets '$(SEED_ASSETS)' \
		--output build/releases/Forge-0.1.0-2-seed.ipa \
		--sequence 1 \
		--public-key Config/ReleasePublicKey.json

validate:
	$(PYTHON) tools/validate_project.py
