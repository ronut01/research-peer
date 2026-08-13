---
name: ask
description: Ask an authenticated teammate Claude a question in the active Research Peer room. Use only when the local owner explicitly invokes /research-peer:ask.
disable-model-invocation: true
---

# Ask a peer

If `$ARGUMENTS` is empty, ask the local owner what they want to ask and stop.

Confirm that this Claude session has exactly one active room binding. If it does not, run `research-peer room list` and ask the owner to choose `/research-peer:use ROOM`.

Send `$ARGUMENTS` as a `QUESTION` with the Research Peer MCP send tool. Show the returned message and request IDs. When an `ANSWER` arrives, match its `request_id` to this question and apply it to the owner's original task.

Send only the question and context the owner explicitly chose. Do not automatically attach transcripts, environment variables, credentials, or private files.
