# Field report — first real two-server pairing (2026-08-18)

First end-to-end pairing between two university lab servers. It eventually
worked, but only after roughly two hours of debugging, four discarded invites, and
one workaround that required reading the SQLite state directly because no CLI command
could show a received message.

This document records every failure encountered, the root cause in the code, and what
should change. Everything here is reproducible from the two accounts involved; nothing
is speculative.

## Environment

| | Local owner | Teammate |
|---|---|---|
| Host | `<LOCAL_HOST>` (personal server) | `<PEER_HOST>` (large shared server) |
| Account | `<LOCAL_USER>` | `<PEER_USER>` |
| Reachability | ICMP yes, all inbound TCP dropped | same |
| sudo | no | no |
| sshd | port `<SSH_PORT>` (lab-wide convention), inbound allowed | port `<SSH_PORT>`, inbound allowed |

Both hosts run `ufw` with `DEFAULT_INPUT_POLICY="DROP"` and neither user can change it.
The only inbound-reachable port on either machine is the lab's SSH port. This is the
normal situation for university lab servers, not an exotic one — the tool currently has
no story for it.

## What actually blocked the pairing

### 1. `receive` is not an inbox reader, but the help text implies it is

`research-peer help` lists `receive` among user-facing commands. Running it prints:

```
research-peer: ERROR: Expecting value: line 1 column 1 (char 0)
```

`cli.py:546` — `receive` does `json.load(sys.stdin)` and feeds the packet to
`PeerDaemon.process_packet`. It is an internal transport entry point. With an empty
stdin it raises `JSONDecodeError`, which the handler at `cli.py:565` renders as the
message above. Exit code is 0, so nothing looks wrong to a caller checking status.

The deeper problem: **there is no user-facing command that reads the inbox at all.**
A teammate's QUESTION arrived, `status` reported `inbox_waiting_for_session: 1`, and the
only way to read the text was:

```python
sqlite3.connect('file:~/.local/state/research-peer/state.db?mode=ro', uri=True)
# select envelope_json from messages where direction='in'
```

This is the single worst gap found. A message can arrive, be acknowledged in `status`,
and still be unreadable through the CLI. The messages did eventually surface through the
Claude channel once the session binding was corrected — but only after the delivery
suppression described in §2b released them, and there was no way to know that from the
tool's own output.

A related observation: the same question was sent twice during bring-up — a manual retest
while the connection was still unstable, not an automatic resend — and both copies were
delivered and surfaced. `replay_nonces` correctly guards against replayed envelopes, but
two genuinely distinct envelopes carrying the same text are both accepted, which is the
right behaviour here and only becomes a question once answering is automated (see the
auto-answer section).

**Fix**
- Rename the stdin transport endpoint to something internal (`_ingest`, or hide it with
  `argparse.SUPPRESS` like `daemon` and `channel` already are).
- Add a real `research-peer inbox [--room ROOM] [--json] [--all]` that lists pending
  inbound messages with sender, type, `request_id`, timestamp, and body text, and a
  `--consume` flag that marks them read.
- When stdin is a TTY, the transport endpoint should refuse with a clear message rather
  than attempting a JSON parse.

### 2. `session register` silently invents a session ID

`cli.py:469`:

```python
session_id = args.session_id or str(uuid.uuid4())
```

It never consults `RESEARCH_PEER_SESSION_ID`, even though `start` exports that variable
(`cli.py:503`) and the `make` / `join` skills instruct the agent to "bind this session to
the room". Following the skill instructions verbatim therefore registers a **phantom
session** that no Claude session will ever poll.

Consequence: inbound messages are stored with `state='no_target_session'`
(`store.py:368`) and are never delivered. The teammate's first QUESTION was lost exactly
this way; they resent it, and the second copy also sat undelivered until the correct
session ID was registered by hand:

```
research-peer session register --session-id "$RESEARCH_PEER_SESSION_ID" --alias research-peer --room <ROOM>
```

Three phantom sessions accumulated in `session list` over the session, all bound to the
same room and all left `active=1`, with no indication which one (if any) was real.

**Fix**
- Default `--session-id` to `os.environ.get("RESEARCH_PEER_SESSION_ID")` before falling
  back to a random UUID.
- When falling back, print a warning that the session is not bound to a live Claude
  session.
- `status` should report the count of registered-but-stale sessions, and `session list`
  should mark which entry matches the current `RESEARCH_PEER_SESSION_ID`.
- Re-registering the same room should retire the previous binding for that alias, or at
  least warn, instead of silently accumulating rivals — see the next item for why that
  matters.

### 2b. Broadcast delivery is suppressed when more than one session is active

This is the sharpest bug found, and it explains the delivery failure better than the
phantom-session count alone.

`store.py:411-417`, in `poll_session`:

```sql
AND (json_extract(envelope_json,'$.to.session')=?
     OR (json_extract(envelope_json,'$.to.session')='' AND ?=1))
```

The second branch is the broadcast case: a message with an empty `to.session` — which is
what `send` produces when no `to_session` is given, i.e. the normal case — is delivered
**only when `active_count == 1`**, where `active_count` counts sessions in the room with
`active=1` and `last_seen` within 300 seconds (`store.py:409-410`).

So with two live Claude sessions in the same room — an entirely ordinary setup, and the
obvious one for a shared lab server — unaddressed inbound messages are withheld from
*everyone* rather than delivered to either. `status` reports them as
`inbox_waiting_for_session`, which reads like "waiting for a session to appear" when the
truth is closer to "refusing to deliver because too many sessions appeared."

The timing here is measurable. The two questions were consumed at `06:38:34`. The
300-second cutoff at that moment was `06:33:34`; the rival session's `last_seen` was
`06:33:33`. Delivery unblocked with **one second** of margin, purely because a stale
session aged past the window. Had that session been polled once more, the messages would
still be undelivered.

`active=1` is never cleared automatically either — only `session prune` does that — so
the rival sessions stay eligible indefinitely as long as something refreshes them.

**Fix**
- Deliver broadcast messages to all live sessions in the room, or to the most recently
  seen one, rather than suppressing them when several exist.
- If suppression is genuinely intended, say so explicitly: `status` should distinguish
  "no session bound" from "delivery blocked by N competing sessions", and the daemon
  should log it.
- Consider auto-deactivating sessions whose `last_seen` exceeds the staleness window
  instead of requiring a manual `prune`.

### 3. `init --listen` does not affect the running daemon, and nothing says so

`_init` (`cli.py:256`) writes `config.json` and returns. `_start_daemon` (`cli.py:277`)
returns `{"already_running": True}` when the pid file exists. So the documented sequence

```
research-peer init --listen HOST:PORT
research-peer start
```

leaves the daemon bound to the **previous** address indefinitely.

This cost the most wall-clock time. After `init --listen <LOCAL_HOST>:<LOCAL_PORT>`, the
daemon was still on `127.0.0.1:<LOCAL_PORT>`; the teammate's join timed out; every diagnostic
pointed at the network. `ss -tlnp` was what finally revealed it — nothing in the tool
did.

**Fix**
- Have `init --listen` detect a running daemon and either restart it or exit with
  `"daemon is running on <old>; run research-peer stop && research-peer start"`.
- Have `start` compare the daemon's actual bind address to `config.json` and warn (or
  restart) on mismatch instead of reporting `already_running`.
- `status` currently derives `daemon.running` purely from pid-file existence
  (`cli.py:511`). It should report the address the daemon is actually listening on, so a
  config/runtime mismatch is visible at a glance.

### 4. `_validate_endpoint` accepts wildcard and loopback addresses in invites

`rooms.py:70-81` checks only that the host is non-empty and the port is 1025–65535. The
teammate ran `init --listen 0.0.0.0:<PEER_PORT>`, and the resulting invite advertised
`"endpoint": "0.0.0.0:<PEER_PORT>"` — an address no remote peer can dial. The invite had to be
discarded and regenerated.

`room join --endpoint` says "local advertised HOST:HIGH_PORT", but nothing prevents a
non-routable value there either.

**Fix**
- Reject `0.0.0.0` and `::` as advertised endpoints in `create_invite` and `join`, with a
  message naming the interface addresses actually available.
- Allow loopback only when the user opts in (it is legitimate under an SSH tunnel — see
  below) via something like `--advertise-loopback`, so the common mistake still errors
  but the tunnel workflow stays possible.

### 5. Invite TTL of 30 minutes is too short for a human-relayed handshake

`cli.py:171` and `rooms.py:18` default `expires_minutes=30`. In practice the invite has to
travel through a person, into a messenger, into the teammate's Claude, and back. Three
invites expired mid-handshake during this session before the connection was even
diagnosable.

**Fix**
- Raise the default to something like 24 hours, or make it configurable in `config.json`.
- Print remaining validity in a human-readable form (`expires in 29m`) alongside the
  timestamp, and have `join` report expiry as its own error code rather than a generic
  failure.

### 6. `doctor` cannot see the actual blocker

`doctor` reports `local_bind`, `loopback`, and `unix_socket` — all of which passed on both
machines while the pairing was completely broken. It has no check for:

- whether a host firewall will drop inbound connections to the advertised port
- whether the configured `listen_host` matches what the daemon is bound to
- whether the advertised endpoint is routable at all

`bidirectional`, `peer`, and `peer_authentication` all reported `not_tested`, which is
accurate but unhelpful — those are exactly the checks that mattered.

**Fix**
- Add a check that reads `config.json`, compares it to the live listener, and fails loudly
  on mismatch.
- Add a local firewall heuristic: `/etc/ufw/ufw.conf` `ENABLED=yes` plus
  `/etc/default/ufw` `DEFAULT_INPUT_POLICY="DROP"` are both world-readable and were
  sufficient to diagnose this case without sudo. `systemctl is-active ufw firewalld
  nftables` also works unprivileged.
- When inbound looks blocked, emit the SSH-tunnel recipe (below) as remediation rather
  than leaving the user to invent it.

### 7. CLI surface inconsistencies

- `research-peer room status <ROOM>` does not exist; the natural guess after
  `room make` fails with an argparse error listing valid choices. `room list` is the
  only inspection command and it shows no peer or connection state.
- `--json` is accepted by `receive` and `doctor` but rejected by `room list` and
  `status`, which always emit JSON anyway. The flag's presence is arbitrary.
- `room delete` without `--yes` in a non-interactive context prints the full dry-run plan
  and *then* errors out — reasonable, but the plan output looks like success and is easy
  to misread as completion.

## The workaround that made it work

Since both hosts allow inbound SSH on `<SSH_PORT>` and both allow outbound freely, the pairing
was completed by tunneling Research Peer traffic through SSH. **No third-party relay is
involved** — the two daemons still speak directly to each other, and the design goal of
"no central relay" is preserved.

Layout:

- local daemon binds `127.0.0.1:<LOCAL_PORT>`
- teammate's daemon binds `127.0.0.1:<PEER_PORT>`
- one SSH connection carries both directions:

```
ssh -N -p <SSH_PORT> -i ~/.ssh/id_ed25519 \
    -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -L <PEER_PORT>:127.0.0.1:<PEER_PORT> \
    -R 127.0.0.1:<LOCAL_PORT>:127.0.0.1:<LOCAL_PORT> \
    <PEER_USER>@<PEER_HOST>
```

- local invite advertises `127.0.0.1:<LOCAL_PORT>`; the teammate reaches it through the reverse
  forward
- teammate joins with `--endpoint 127.0.0.1:<PEER_PORT>`; the local daemon reaches them through
  the forward

The key was restricting the authorized key rather than handing over shell access:

```
restrict,port-forwarding,permitopen="127.0.0.1:<PEER_PORT>",permitlisten="127.0.0.1:<LOCAL_PORT>" ssh-ed25519 AAAA... comment
```

**One trap worth documenting prominently.** With `permitlisten="127.0.0.1:<LOCAL_PORT>"`, the
short reverse-forward form fails:

```
ssh -R <LOCAL_PORT>:127.0.0.1:<LOCAL_PORT> ...
# Error: remote port forwarding failed for listen port <LOCAL_PORT>
```

sshd treats a bind-address-less request as `*:<LOCAL_PORT>`, which does not match a
`permitlisten` that names `127.0.0.1`. The bind address must be explicit:

```
ssh -R 127.0.0.1:<LOCAL_PORT>:127.0.0.1:<LOCAL_PORT> ...
```

This error message points at the wrong thing — it reads as a port conflict on the remote
host, and time was spent looking for a stale daemon there. Worth a FAQ entry.

## Requested feature: unattended answering

The local owner's stated goal after this session: **a peer's QUESTION should be answered
by Claude Code automatically, without the owner having to read it and dictate a reply.**
In this session every step was manual — the question had to be extracted from SQLite,
shown to the owner, approved verbatim, and sent by hand.

This is a reasonable goal, but it cannot simply be switched on, because peer messages are
authenticated *untrusted* input (`CLAUDE.md`, and the channel's own banner). An agent that
answers automatically is an agent that discloses automatically. The design needs three
things the tool does not currently have.

### The loop guard exists but is never armed

`protocol.py:15` defines `MAX_AUTOMATION_DEPTH = 4` and `protocol.py:159` rejects
envelopes exceeding it. But **nothing ever increments the field.** `new_envelope`
(`protocol.py:173`) defaults `automation_depth=0`, the `send` call site (`cli.py:530-536`)
never passes it, and the MCP `research_peer_send` schema
(`channel/research-peer-channel.mjs:50-65`) does not expose it at all. Every message on
the wire is depth 0.

Today that is harmless because a human is in the loop on both ends. The moment both sides
auto-answer, two agents will answer each other's answers indefinitely with no ceiling —
the guard that was designed to stop exactly this will pass every message.

**Fix**
- Increment `automation_depth` when a message is generated in response to a received
  message, and carry the incoming depth into the reply.
- Expose it in the MCP send tool so the agent-side path is covered, not just the CLI.
- Treat exceeding the cap as "stop and notify the owner", not as an error to retry.

That said, depth counting should **not** be the primary loop defense — see the next
section, which closes the loop structurally and reduces depth to a backstop.

### Terminal replies: the loop is better closed by message type

The owner's proposal, and it is a better fit than depth counting: **make replies
terminal.** An `ANSWER` may be generated automatically in response to a `QUESTION`, but an
`ANSWER` can never itself trigger an automatic response. If the receiving side finds the
answer insufficient, a human there decides to ask again, and that new `QUESTION` starts a
fresh, bounded exchange.

The conversation is then bounded by construction rather than by a counter: every
auto-exchange is at most one question and one answer, and no amount of message volume can
produce a self-sustaining loop.

Most of the machinery for this already exists and is not being used:

- `ALLOWED_TYPES` (`protocol.py:12`) already separates `QUESTION` from `ANSWER`.
- `reply_required` already defaults to `message_type == "QUESTION"`
  (`protocol.py:186`), so an `ANSWER` already arrives carrying `reply_required: false`.
  Nothing currently consumes that signal — the field is transmitted and ignored.
- `ANSWER` already must carry the originating `request_id` (`protocol.py:138`), so
  question-and-answer pairing is enforced at the schema level.

**The one invariant that has to hold**

> Automatic generation may emit `ANSWER` only. An agent must never auto-emit a `QUESTION`.

This is where the design can quietly fail. If an agent, on receiving an unsatisfying
`ANSWER`, auto-sends a new `QUESTION` to get better information, the loop is back — just
one type removed, and now with fresh `request_id`s that defeat any pairing check. The
"ask again" decision must belong to a person, or at minimum require explicit
owner_attention. This should be stated as a hard rule in the skill, not left to agent
judgment.

**Scope: only `QUESTION` is auto-answerable**

`reply_required` already defaults to true only for `QUESTION` (`protocol.py:186`).
`HANDOFF`, `STATUS`, and `ARTIFACT_REF` are informational and expect no reply, so
auto-answering simply does not apply to them — they are surfaced to the owner as context
and nothing is sent back. This needs no new protocol work, only that the auto-answer path
gate on `type == "QUESTION" and reply_required`, rather than on "a message arrived".

That keeps the rule small enough to state in one line, which matters because it has to
hold in the skill instructions as well as the code.

**Answer each `request_id` at most once**

The duplicate question observed in this session came from manual retesting while the
connection was still being brought up, not from an automatic resend — so it is not
evidence of a resend loop, and content-level deduplication is not needed for it.

What is still worth doing is the trivial version: record which `request_id`s have been
auto-answered and never answer the same one twice. `ANSWER` already carries the
originating `request_id` (`protocol.py:138`), so this is a uniqueness check on data the
tool already has, and it makes auto-answering idempotent if a question is redelivered
after a transport retry — which is a real possibility given the delivery failures in §1
and §2b.

Content-level dedup (same text, different `request_id`) can wait until there is evidence
it happens outside of testing.

**Deferred: rate limiting**

Terminal replies bound the length of an exchange but not its rate, so a per-room cap on
auto-sent messages is a sensible backstop eventually. Deliberately deferred for now — it
adds a failure mode of its own (silently dropped answers) and there is no observed
volume problem to solve. Revisit if a room ever sees automated traffic from more than one
peer, or if a peer's agent begins initiating questions.

**Keep `automation_depth` armed regardless**

It costs little and catches the case where the "never auto-emit a `QUESTION`" invariant is
violated by a future change.

### Disclosure scope has to be explicit

A QUESTION like the one received here — "what experiments are you running?" — is exactly
the case where an agent left to its own judgment might answer with container names, GPU
allocations, file paths, or project internals. The owner's approved answer in this session
was deliberately minimal ("v2 in progress"). That gap between "what the agent could say"
and "what the owner wanted said" is the whole risk.

**Proposal**
- A per-room disclosure policy in `config.json`, e.g. `disclosure: "none" | "status" |
  "summary" | "full"`, defaulting to `status`.
- `status` permits liveness and coarse progress only. `summary` permits a project
  description the owner has written in advance. `full` permits the agent to compose from
  project files, and should require an explicit opt-in per room.
- A standing owner-authored blurb (`room note`) that the agent may quote verbatim at
  `summary` level. This directly matches how the owner actually answered here — one
  approved sentence, reusable.
- Never auto-answerable regardless of level: credentials, environment variables,
  transcripts, file contents, invite tokens, endpoints, anything under `~/.ssh`, and any
  question asking the agent to run a command or change configuration.

### Escalation and auditability

- `owner_attention` already exists in the protocol and is plumbed through
  (`cli.py:534`); use it as the signal to break out of auto-answering and surface to the
  owner instead.
- Anything the agent cannot answer within the room's disclosure level should escalate
  rather than be answered vaguely — a wrong-but-confident auto-answer is worse than a
  delay.
- Every auto-sent message needs an audit trail the owner can read after the fact:
  question, reply, disclosure level applied, timestamp. There is currently no command that
  shows sent message bodies at all.
- The owner should be able to disable auto-answering per room without leaving the room.

## Onboarding: the first connection was the hardest part

Worth stating plainly, because it is the strongest signal from this session: **two
competent users with a working network between them could not connect two servers without
several hours of assistance.** ICMP worked between the machines the entire time. Nothing
was misconfigured by the users. Every obstacle was the tool's.

The sequence actually experienced, in order:

1. `init` accepted an endpoint, `room make` produced an invite — but the daemon was still
   bound to the old address, so the invite pointed somewhere nothing was listening. No
   output from any command indicated this. (§3)
2. The invite expired while the failure was being diagnosed. Twice more after that. (§5)
3. `doctor` reported healthy throughout. (§6)
4. The teammate's invite advertised `0.0.0.0`, which was accepted at creation and only
   failed at dial time. (§4)
5. Once a tunnel was up, the room connected — and the first question still did not arrive,
   for two further reasons (§1, §2b) neither of which produced a diagnosable message.

A first-run experience that survives this needs to be **verification-first**: never hand
the owner an invite that has not been proven dialable.

**Proposal: `research-peer connect`**

One guided command that refuses to produce an invite until the path works:

1. Read the config, compare against the live listener, restart the daemon if they differ.
2. Enumerate local interfaces; reject wildcard and unreachable advertised addresses.
3. Probe the advertised endpoint from outside the loopback path where possible; check the
   unprivileged firewall heuristics from §6.
4. If inbound is blocked, do not fail — offer the ordered fallbacks: an already-open port,
   then the SSH tunnel (with the `permitlisten` bind-address form baked in), then an admin
   request as the last resort, which is where it belongs rather than the first suggestion.
5. Only then mint the invite, and print its remaining validity in relative terms.

The same ordering belongs in the `make` and `join` skills, which currently ask for
`HOST:PORT` and assume the answer will work.

A companion `research-peer verify <ROOM>` that does a real round trip — send a probe,
confirm the peer's daemon answered, confirm a message can be delivered to a registered
session — would have caught §1, §2b, and §3 in one command.

### Structured requests for information the peer holds

Setup is inherently two-sided, and this is where most of the wall-clock actually went. The
tool has a chicken-and-egg problem: the information needed to establish the channel cannot
be exchanged over the channel. Every one of these had to travel through a person:

| Needed from the peer | How it went |
|---|---|
| Their advertised endpoint | First answer was `0.0.0.0:<PEER_PORT>`, unusable, discovered only at dial time |
| Their real interface address | Required a second round trip |
| Their SSH port and login name | Two more round trips; the SSH port turned out to be a lab-wide convention nobody had written down |
| Their public-key fingerprint | Needed for out-of-band verification before authorising the tunnel |
| Confirmation their daemon was actually listening | Never volunteered; had to be asked for explicitly, twice |

Each of these was handled by hand-writing a long instruction block for the peer's Claude
Code, in the local owner's language, and asking the owner to relay it. That worked, but it
was improvised each time, and one round trip was wasted because the block did not say
"the `echo` must stay on one line."

The `make` skill already establishes the right pattern: when it needs the room name, it
stops and asks the owner rather than inventing one. The same pattern should exist for
information that lives on the *other* side.

**Proposal: a peer-prerequisites exchange**

- `research-peer request-info [--for tunnel|endpoint|verify]` emits a ready-to-relay block
  containing the exact commands the peer should run and the exact values to send back,
  with the pitfalls already baked in (one-line `echo`, restart the daemon after `init`,
  confirm with `ss -tln`).
- The block should be generated in the owner's language, since it is written for a human
  to paste to another human.
- `research-peer accept-info` (or a paste target in the skill) parses the returned values,
  validates them — reject `0.0.0.0` immediately rather than at dial time, check the port
  is in range, check the fingerprint is well-formed — and writes them into the pending
  pairing state.
- The skill should then tell the owner exactly what is still missing, in the same way
  `make` reports a missing room name, instead of failing at the next command.

Fingerprint verification deserves special handling. The teammate's Claude correctly
refused to install the key until the fingerprint was confirmed out of band, which was the
right call and is worth keeping — but the flow around it was entirely improvised. It
should be a first-class step: the tool prints the fingerprint, states plainly that it must
be read over a different channel than the one carrying the key, and records that the
confirmation happened.

## What this would and would not have fixed

Being honest about the limits, since the goal is that the next pairing "just works":

**Would have been eliminated**

- The stale-daemon trap (§3), the `0.0.0.0` invite (§4), the expired invites (§5), the
  false-clean `doctor` report (§6) — each of these is a check the tool can perform on its
  own with information it already has.
- The unreadable inbox (§1) and the suppressed delivery (§2b) — pure implementation bugs.
- Most of the improvised relay blocks, if the prerequisites exchange above exists.

**Would still require human decisions**

- Both hosts drop all inbound TCP and neither user has sudo. No amount of tooling changes
  that. A `connect` command can detect it in seconds instead of an hour and can hand over
  the exact tunnel recipe, but somebody still has to decide to authorise an SSH key on the
  other machine, and somebody still has to confirm a fingerprint out of band. That is
  correct — those are trust decisions, not configuration.
- Coordination latency. The two sides must act in a specific order, and the peer's agent
  has to actually carry out its half.

The realistic target is not "no human involvement" but **"minutes of human involvement,
each step signposted"** — instead of hours of undiagnosable failure. Everything in this
document is in service of that.

## Recommended additions to the tool

Beyond the individual fixes above:

1. **A tunnel mode.** Something like `research-peer tunnel --via ssh USER@HOST:PORT` that
   sets up loopback binding, launches the forwards with the correct explicit bind
   addresses, and keeps them alive. This scenario — two firewalled lab servers, no sudo,
   SSH as the only ingress — is likely the common case for the tool's actual audience,
   not an edge case.
2. **Connection state in `room list` or a new `room status`.** Peer fingerprint, endpoint,
   last successful exchange, pending inbound count. During debugging there was no way to
   answer "is the peer connected right now" without cross-referencing `peer list`,
   `status`, and the daemon log.
3. **Better daemon logging.** The entire log for a two-hour debugging session was two
   lines:
   ```
   WARNING inbound failure: SSLEOFError
   WARNING inbound failure: EOFError
   ```
   No peer address, no room, no direction, no reason. The `EOFError` was in fact the
   teammate's first QUESTION failing to deliver, which would have been diagnosable
   immediately with a sender and a room ID attached.
4. **Setup guidance for the no-sudo case.** The skill flow currently asks for
   `HOST:PORT` and assumes it will be reachable. It should verify reachability, and when
   it fails, walk through the ordered alternatives (already-open ports → SSH tunnel →
   admin request) instead of leaving the agent to derive them.

## Suggested order of work

The items are not equally urgent. Proposed sequence:

1. **§1 inbox command** and **§2/§2b delivery** — without these a delivered message can be
   invisible, which undermines the tool's purpose more than any setup friction.
2. **§3 init/daemon mismatch** and **§6 doctor checks** — the two that consumed the most
   time and are cheap to fix.
3. **§4 endpoint validation** and **§5 invite TTL** — small, self-contained, remove two
   whole classes of discarded-invite churn.
4. **Terminal-reply rule plus `automation_depth` enforcement** — prerequisites for
   auto-answering, and worth landing even if that feature slips. The terminal-reply rule
   is mostly a matter of honouring `reply_required`, which is already on the wire.
5. **`connect` / `verify` guided flow, plus the peer-prerequisites exchange** — the
   largest piece, but it subsumes much of the value of 2 and 3 by making failures
   self-diagnosing, and it is what turns a multi-hour bring-up into a signposted one.
6. **Unattended answering** — last, and only on top of 1, 4, and an explicit disclosure
   policy.

## Documentation gaps observed

- `docs/operations.md` should carry the SSH-tunnel recipe and the `permitlisten` trap.
- The `make` skill should tell the agent to verify the listener with `ss -tln` after
  `init`, since `init` alone is silently insufficient.
- The `join` skill should state that `--endpoint` is mandatory under a tunnel and explain
  which side's address it refers to; the current help text ("local advertised
  HOST:HIGH_PORT") was misread during this session as the peer's address.
- Nothing in the docs explains that messages are delivered to a *registered session*
  rather than to a room. That model is load-bearing and invisible until delivery fails.

## Verified working after fixes applied by hand

- Tunnel established, both directions confirmed with `nc`
- Room `test` (`<ROOM_UUID>`) created and joined
- Teammate's QUESTION recovered from the store and answered with an `ANSWER` carrying the
  original `request_id`; delivery state `delivered`
- Both queued questions later surfaced through the Claude channel on their own once the
  competing sessions aged out, confirming §2b

The transport and crypto layers behaved correctly throughout. Every failure in this
session was in setup ergonomics, state visibility, or diagnostics.
