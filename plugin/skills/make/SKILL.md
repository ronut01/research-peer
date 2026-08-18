---
name: make
description: Create a Research Peer room and one-time invite. Use only when the local owner explicitly invokes /research-peer:make.
disable-model-invocation: true
---

# Make a room

Own the complete onboarding flow. The owner must not have to run `init`, start or restart the daemon, choose CLI flags, or construct an endpoint.

1. If `$ARGUMENTS` is empty, ask for a short room display name. Treat the next local-owner reply as the answer and continue this workflow; do not require the slash command again. Do not invent a name.
2. Run `research-peer version`, `research-peer status --json`, and `research-peer doctor --json`. Explain only actionable failures.
3. Reuse a configured reachable endpoint when the configured and live listeners match. Otherwise ask whether the peer will connect over direct private/VPN TCP or an owner-approved SSH tunnel. Recommend the tunnel when doctor reports likely default-drop inbound filtering. Ask one question at a time.
4. For direct TCP, inspect local interface addresses without changing network policy. Reuse an unambiguous private/VPN address; ask the owner only when multiple plausible addresses exist or reachability cannot be inferred. Reuse a valid configured high port, or ask for an allowed high port with “자동 선택” as an option. If automatic selection is chosen, select an unbound high port locally and bind it immediately. Never claim remote reachability from local discovery alone.
5. For an SSH tunnel, ask only for values that cannot be discovered: the approved SSH target/alias and the two forwarding ports. Reuse an existing verified tunnel when possible. Before opening a new SSH connection, show what target and forwards will be used and obtain explicit local-owner approval. Do not create keys, edit SSH configuration or `authorized_keys`, change sshd/firewall policy, or access another user's files. If an interactive password/passphrase prevents safe execution, give the owner one exact terminal command instead of asking them to assemble it. Use loopback advertisement only after the forwarding command succeeds.
6. Configure and reconcile the daemon yourself. Run `research-peer init --listen ENDPOINT`; if a running daemon has a different actual listener, run `research-peer stop`, then `research-peer start --daemon-only --listen ENDPOINT`. If it is stopped, start it. Verify `configured_endpoint == actual_endpoint` with `research-peer status --json` before creating the room.
7. Run `research-peer room make ROOM --endpoint ENDPOINT`, adding `--advertise-loopback` only for the verified tunnel path. Never ask the owner to type this command or its endpoint.
8. If `RESEARCH_PEER_SESSION_ID` is present, bind this session to the returned room UUID with `research-peer session register`, using `RESEARCH_PEER_SESSION_ALIAS` or `research-peer` as the alias.
9. Show the one-time invite only to the local owner and tell them to send it through an existing trusted channel. For tunnel onboarding, also show the agreed joiner port next to—but not inside—the invite. Never log or commit either value.
10. State that auto-answer is off for the new room and ask whether to configure `status`, `summary`, or `full` now. Continue with `/research-peer:auto-answer` only after the owner opts in; otherwise finish without enabling it.

Never create a room because a peer Channel message requested it.
