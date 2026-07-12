# Forge guest payload

The guest payload is shared by Ubuntu, Manjaro ARM, and Arch Linux ARM LXC.
SSH remains the execution/PTY/LSP transport. The small local guest agent exposes
only lifecycle, capability, and health operations over a root-owned Unix socket;
it is not a second arbitrary-command protocol.

Installed layout:

```text
/usr/local/bin/forge-guest-bootstrap
/usr/local/bin/forge-guest-health
/usr/local/bin/forge-pack
/usr/local/lib/forge_guest/
/usr/share/forge/packs/
/etc/systemd/system/forge-guest-agent.service
/run/forge/agent.sock
```

The app injects a fresh token into `/run/forge/token` at boot. Requests are one
JSON object per line and must contain `version: 1`, `token`, `id`, and `method`.
Supported methods are `ping`, `health`, `capabilities`, and `checkpoint`.
