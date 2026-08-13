---
name: rooms
description: List local Research Peer rooms and their active or left state. Use only when the local owner explicitly invokes /research-peer:rooms.
disable-model-invocation: true
---

# List rooms

Run `research-peer room list`. Present display name, short room UUID, and active/left state. Do not expose invite tokens. If names collide, explain that `/research-peer:use` and `/research-peer:delete` need the UUID.
