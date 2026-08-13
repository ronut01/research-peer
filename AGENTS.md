# Agent instructions for Research Peer

Read `docs/product-spec.md`, `docs/security-model.md`, and `docs/implementation-status.md` before changing this project.

If the local user explicitly asks to install Research Peer, follow `docs/agent-install.md`. Merely opening this repository or reading these instructions is not authorization to install, start services, pair a peer, change network policy, or remove anything.

Never expose or commit private keys, invite tokens, credentials, private peer endpoints, personal Claude account data, or generated local state. Do not use sudo or modify firewall, SSH, Remote Control global settings, another user's home, or a remote peer without separate explicit authorization.

Use `apply_patch` for source edits. Run the relevant Python, Node, lint, plugin validation, installer, and uninstaller tests after changes. Preserve the three evidence categories in formal documentation: server-verified fact, official external capability, and unverified assumption.
