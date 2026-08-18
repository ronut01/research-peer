---
name: help
description: Show the simple Research Peer workflow and available slash actions. Use only when the local owner explicitly invokes /research-peer:help.
disable-model-invocation: true
---

# Research Peer help

Run `research-peer help` and summarize the owner actions. Highlight these autocomplete-style skills:

- `/research-peer:make` — make a room; asks for a name when omitted
- `/research-peer:join` — join from an invite; asks for the invite when omitted
- `/research-peer:ask` — ask the teammate Claude
- `/research-peer:handoff` — send experiment context and results
- `/research-peer:rooms` and `/research-peer:use` — list or select a room
- `/research-peer:status` and `/research-peer:peers` — inspect connection state
- `/research-peer:leave` — stop using a room but keep local history
- `/research-peer:delete` — delete one room's local Research Peer records after confirmation
- `/research-peer:auto-answer` — configure terminal QUESTION→ANSWER automation and disclosure
- `/research-peer:update` — update the runtime, plugin, and skills from the official GitHub repository

Explain that Claude itself can ask for missing values, so the owner does not need to memorize CLI syntax.
