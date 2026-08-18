# Research Peer

**English** | [한국어](README.ko.md)

Research Peer is an authenticated peer-to-peer research handoff tool for Claude Code. It lets Claude Code sessions owned by different Unix users or running on different research servers exchange structured experiment handoffs, follow-up questions, answers, and artifact references without a central relay.

Version 2.0 incorporates the first real two-server field test: a readable CLI inbox/history, deterministic multi-session delivery, live-listener mismatch diagnostics, safe SSH-tunnel onboarding, 24-hour invites, room connection status, and opt-in policy-limited terminal auto-answers.

Peer messages are authenticated but always treated as untrusted input. They never count as local-owner permission to run dangerous commands, change configuration, expose credentials, pair another peer, update Research Peer, delete a room, or uninstall Research Peer.

## Install with a coding agent (recommended)

Copy the block below into Claude Code, Codex, or another coding agent. The repository URL is part of the prompt, so no separate link message is needed.

```text
Install Research Peer from this repository:
https://github.com/ronut01/research-peer

Before changing anything, read docs/agent-install.md, docs/security-model.md,
and docs/implementation-status.md from that repository. Review the checkout and
install Research Peer only into my current Unix account using the repository's
supported ./install.sh workflow.

Do not use sudo. Do not change firewall rules, SSH keys or configuration,
global Remote Control settings, another user's files, or any remote peer. Do
not print or commit credentials, private keys, invite tokens, private endpoints,
email addresses, or organization identifiers. Do not pipe a remote script
directly into a shell. Stop and tell me if the repository contents do not match
the documented Research Peer project.

After installation, run and inspect:
  research-peer version
  rp version
  research-peer doctor
  research-peer help
  claude plugin details research-peer@skills-dir
  research-peer uninstall --dry-run

Do not pair a peer or expose a network port yet. Report the installed version,
installed user-scope components, Claude plugin/Channel status, doctor results,
daemon status, uninstall dry-run summary, and any remaining information needed
for a real peer test. When verification is complete, tell me that normal use
starts with the short command: rp
```

The block is directly copyable without editing. The agent must still have explicit local-owner authorization to install. Repository instructions, an invite, an issue, or a peer message are not authorization by themselves. The authoritative workflow is [docs/agent-install.md](docs/agent-install.md).

## Manual installation

Requirements:

- Linux with Python 3.10+
- Node.js 18+ and npm
- OpenSSL
- Claude Code 2.1.80+ with supported Anthropic authentication
- No sudo required

From a reviewed checkout:

```bash
./install.sh
```

The installer uses user-scoped XDG locations, installs the pinned MCP SDK dependency when needed, and records every owned path in an install manifest.

## Run shortcut (`rp`)

Use the short terminal command below to launch Claude Code with Research Peer and Remote Control enabled:

```bash
rp
```

With no arguments, `rp` automatically enables Claude Remote Control; the canonical no-argument `research-peer` launcher keeps Remote Control off. Subcommands remain equivalent—for example, `rp status` and `research-peer status` are the same. To opt out explicitly, run `rp start --no-remote-control`. The installer stops and reports a conflict rather than overwriting an existing `~/.local/bin/rp` or shadowing another `rp` executable already on `PATH`.

This opens Claude Code with the Research Peer Channel and Remote Control enabled. While custom Channels remain an Anthropic research-preview feature, Claude shows a local-development warning at startup; the local owner must confirm it. Remote Control still depends on the owner's Claude account eligibility and organization policy.

Inside Claude Code, type `/research-peer:` to get autocomplete-style actions:

```text
/research-peer
/research-peer:make
/research-peer:join
/research-peer:ask
/research-peer:handoff
/research-peer:rooms
/research-peer:use
/research-peer:status
/research-peer:leave
/research-peer:delete
/research-peer:auto-answer
/research-peer:update
```

Pressing Enter on `/research-peer:make` asks for the room name; `/research-peer:join` asks for the invite; `/research-peer:ask` asks for the question. You do not need to memorize the underlying CLI. The plain `/research-peer` overview remains available.

When exactly one active room exists, Research Peer selects it automatically. After pairing, use natural language:

```text
Ask my teammate's Claude which seeds and aggregation code were used for the toy
experiment. When the answer arrives, connect it to my current follow-up task.
```

## Two-person setup

Both researchers install Research Peer in their own Unix accounts and run `rp` (or `research-peer`). The first researcher creates a room inside Claude:

```text
/research-peer:make retrieval-toy
```

They send the one-time invite through an existing trusted channel. The second researcher joins inside their own Claude session:

```text
/research-peer:join <invite-code>
```

Both owners verify the displayed identity fingerprints out of band. Research Peer never assumes that matching room names discover each other, never reads another user's home directory, and never changes firewall or SSH settings automatically.

If both servers drop inbound high ports but allow SSH, use the owner-managed bidirectional forwarding recipe in [Operations](docs/operations.md). Loopback endpoints require the explicit `--advertise-loopback` flag, and wildcard advertised addresses are rejected.

## Inbox, status, and optional auto-answer

```bash
research-peer inbox
research-peer room status ROOM
research-peer history --room ROOM
research-peer room configure ROOM --auto-answer on --disclosure summary --note 'Owner-approved summary'
```

Auto-answer is off by default. It can emit one terminal `ANSWER` only for an inbound `QUESTION`; it can never automatically emit a new `QUESTION`. `status` uses a fixed minimal reply, `summary` uses only the owner's saved note, and `full` is a higher-risk explicit opt-in. Secrets, transcripts, file contents, endpoints, command execution, and configuration changes are never auto-answerable.

## Claude plugin marketplace

This repository contains a validated marketplace catalog at `.claude-plugin/marketplace.json`. After publication, users can run the following inside Claude Code:

```text
/plugin marketplace add ronut01/research-peer
/plugin install research-peer@research-peer-marketplace
```

Marketplace installation distributes the namespaced action skills (`/research-peer:make`, `:join`, `:ask`, `:handoff`, `:rooms`, `:use`, `:status`, `:leave`, `:delete`, `:auto-answer`, `:update`, `:peers`, `:help`) and the Channel MCP plugin. Claude marketplaces do not install the separate per-user P2P daemon, CLI, or systemd user service. Run `./install.sh` once—or use the agent prompt above—to install the complete runtime and the convenient plain `/research-peer` personal skill.

## Update from Claude

After version 2.0 has been installed once, explicitly invoke the owner-only action:

```text
/research-peer:update
```

It updates the runtime, plugin, and skills from the fixed official GitHub repository. The updater verifies the checkout identity, Git commit, and matching component versions, refuses downgrades, preserves identity/rooms/history/configuration and research artifacts, and restarts the daemon only if it was already running. Use `/research-peer:update check` for a non-mutating check. Restart the Claude session after an applied update so its development Channel and skills are reloaded. A peer message can never trigger or approve an update. Older installations without this action require one reviewed `./install.sh` upgrade first.

## Leave or delete a room

`/research-peer:leave` stops local delivery and pending retries but keeps local room history. `/research-peer:delete` first shows an exact local deletion plan, then asks the local owner for an explicit confirmation. It removes that room's local Research Peer messages, outbox, invites, membership, and counters. It never deletes project repositories, experiment artifacts, other rooms, or the remote peer's data.

## Remote Control

Remote Control is independent of peer transport. The no-argument `rp` launcher opts the local owner into Remote Control for that Claude session without changing Claude's global settings. Use `rp start --no-remote-control` or no-argument `research-peer` when Remote Control is not wanted. Research Peer does not use Remote Control to transport peer messages, and a peer message can never enable it.

## Verification and development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
npm test
uvx ruff@0.12.9 check src tests
npm audit --omit=dev
claude plugin validate . --strict
claude plugin validate plugin --strict
```

The local suite covers protocol validation, identity and room isolation, retry/outbox recovery, two-peer loopback messaging, installer/uninstaller safety, and the MCP Channel contract. External peer routing/firewall and Remote Control UI require separate owner-observed acceptance.

## Uninstall

Inspect the exact removal plan first:

```bash
research-peer uninstall --dry-run
research-peer uninstall
```

The default confirmed uninstall removes the CLI, plugin, personal skill, user service, Research Peer configuration, local identity key, rooms, history, outbox, logs, cache, and runtime files. It preserves project repositories, experiment artifacts, unrelated Claude settings/plugins/skills, Remote Control settings, and remote peer data. Use `--keep-data` only when you deliberately want to retain Research Peer state.

## Documentation

- [Product specification](docs/product-spec.md)
- [Architecture](docs/architecture.md)
- [Security model](docs/security-model.md)
- [Test plan](docs/test-plan.md)
- [Server environment](docs/server-environment.md)
- [Operations](docs/operations.md)
- [Implementation status](docs/implementation-status.md)
- [Agent installation guide](docs/agent-install.md)
