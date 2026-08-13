---
name: handoff
description: Send a structured experiment handoff to an authenticated Research Peer teammate. Use only when the local owner explicitly invokes /research-peer:handoff.
disable-model-invocation: true
---

# Send a handoff

If `$ARGUMENTS` is empty, ask which experiment or result should be handed off and stop.

Inspect only files and repository state needed for the local owner's named experiment. Draft the structured HANDOFF fields required by Research Peer: objective, hypothesis, repository/remote/branch/commit/modified files, data/model/checkpoint, exact command/environment, seeds/hyperparameters, metrics/aggregation, raw logs/artifact references, successes, failed attempts, confirmed facts, interpretations, unverified assumptions, remaining questions, and cautions.

Show the draft and ask the local owner to confirm any sensitive references. Then send it with the Research Peer MCP handoff tool. Never automatically transmit transcripts, credentials, environment variables, arbitrary home paths, or file contents.
