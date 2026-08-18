# 구현 상태와 복구 체크포인트

마지막 갱신: 2026-08-18 KST
현재 phase: **v1.1 `rp` shortcut, room cleanup, and slash action UX locally verified; peer acceptance pending**

session compaction/restart 뒤 이 파일을 먼저 읽는다. 제품 요구사항은 `docs/product-spec.md`, 설계 결정은 `docs/architecture.md`, 안전 경계는 `docs/security-model.md`, 검증 matrix는 `docs/test-plan.md`가 authority다.

## 완료된 구현

- server OS/user/runtime/network/service/Claude read-only 조사
- Claude Code 2.1.231와 Anthropic 공식 Channels/Remote Control/plugin/skills/MCP 검증
- direct TLS TCP + certificate fingerprint pin + ECDSA-signed envelope transport
- `research-peer doctor`: bind/loopback/Unix socket, Claude flags, systemd/linger, SSH, TLS/protocol/auth/fingerprint, reciprocal result 분류
- strict protocol/HANDOFF schema, payload/timestamp/version/type/automation-depth 검증
- EC identity, one-time expiring invite, UUID room/display-name collision 처리
- authenticated room membership, replay/dedup/rate/request-loop 방어
- SQLite WAL inbox/outbox, durable ACK, exponential retry, daemon restart recovery
- exact session routing, no fallback, heartbeat/stale/prune, room leave 격리
- official MCP SDK stdio Channel, explicit provenance, safe send/status tools, permission relay 없음
- skills-directory plugin과 plain personal `/research-peer` skill
- short `rp` alias plus canonical `research-peer` smart launcher, guided slash UX, rich help, Remote Control opt-in, explicit continue/resume
- user systemd 우선, tmux fallback, rotating logs, stale PID/runtime cleanup
- manifest installer, default full local removal/keep-data safe uninstaller, residue scan
- README와 formal docs 6개
- strict-valid local Claude marketplace catalog와 namespaced plugin skill
- dependency-bootstrap install script와 shareable release archive
- agent-safe `AGENTS.md`, `CLAUDE.md`, `docs/agent-install.md` URL-driven installation workflow
- English-primary `README.md` plus linked Korean `README.ko.md`; publication-time direct repository URL placeholders
- unrelated server-specific gateway references removed from product code, help, skills, and documentation
- 현재 user scope에 Research Peer 1.1.0 설치; user service enabled and running

## v1.1 진행 중 변경

- `research-peer room make` alias와 exact-plan `room delete [--dry-run] [--yes]`
- room leave 시 pending/attempting outbox를 즉시 cancelled 처리
- delete 시 선택 room의 local message/outbox/invite/membership/counter만 transaction 삭제; 다른 room/remote/project 보존
- `/research-peer:make`, `:join`, `:ask`, `:handoff`, `:rooms`, `:use`, `:status`, `:leave`, `:delete`, `:peers`, `:help` 독립 plugin skills
- 인자 없는 make/join/ask/use/leave/delete가 필요한 값 하나를 질문하는 guided UX
- installer가 plugin action skill tree 전체를 manifest에 기록해 설치하도록 갱신
- 실제 plugin inventory에서 skills 12개와 MCP 1개 발견
- 실제 설치된 Claude Code에서 인자 없는 `/research-peer:make`가 room 이름을 질문함

## 검증 결과

- PASS: current-server `LOCAL_BIND_OK`, `LOOPBACK_OK`, `UNIX_SOCKET_OK`
- PASS: user systemd running, Linger=yes, tmux fallback available
- PASS: isolated 2-peer TLS pairing/HANDOFF/QUESTION/ANSWER/request correlation
- PASS: offline outbox → recovery → one receiver effect
- PASS: authenticated doctor probe, fingerprint mismatch, one-way result classification
- PASS: room isolation, exact session, leave, rate/loop boundaries
- PASS: actual stdio MCP initialize/listTools handshake; `claude/channel` present, permission relay absent
- PASS: Python unittest 24
- PASS: Node tests 4
- PASS: Ruff 0.12.9, Python compileall, Claude plugin validation
- PASS: npm audit production dependency vulnerabilities 0
- PASS: isolated install/reinstall/default-full-removal/dry-run/keep-data/residue/idempotence/unrelated preservation
- PASS: actual user install, enabled service, systemd start/status/stop, actual uninstall dry-run
- PASS: actual plugin inventory reports one connected Channel MCP server with two tools
- PASS: two simultaneous real Claude Code processes; A QUESTION injected into B, B ANSWER sent through MCP with preserved request ID, A incorporated it in context
- PASS: actual plain `/research-peer help` personal skill invocation
- PASS: actual one-word `research-peer` launch opened Claude Code 2.1.231 with the Research Peer development Channel selected
- PASS: **[SERVER-VERIFIED]** current user install resolves `rp` to the canonical launcher and reports the same 1.1.0 version; installer tests include it in uninstall/residue handling and refuse both overwrite and PATH shadowing conflicts
- PASS: installed plugin inventory found skills 12 and MCP Channel 1
- PASS: archive without node_modules clean install fetched locked dependency, installed CLI Channel handshake exposed two tools, default uninstall left no residue
- PASS: public distribution content sanitized and published from a separate staging checkout to `https://github.com/ronut01/research-peer`
- NOT TESTED: actual external peer connectivity, real bidirectionality, firewall
- NOT TESTED: Remote Control mobile/browser session or push behavior

## 남은 외부 acceptance

1. 실제 peer host/account/high port/fingerprint로 양방향 direct TCP와 firewall/routing 판정
2. 상대 owner가 invite join/fingerprint를 out-of-band 확인
3. opt-in Remote Control을 각 owner의 모바일/브라우저에서 확인
4. SSH target이 제공되면 direct TCP 실패와 SSH diagnostic을 비교

credential/private key/password/token은 요청하지 않는다.

## Known limitations

- 실제 peer server와 firewall 정보가 없어 external direct TCP/bidirectionality를 검증하지 못했다.
- Channel research preview는 startup confirmation을 필요로 한다. 현재 개인 계정에서는 통과했지만 다른 조직의 policy는 별도다.
- custom Channel이 Anthropic approved allowlist에 들어가기 전에는 one-word launcher 뒤 Claude의 development-channel warning에서 local owner가 Enter로 확인해야 한다. installer가 이를 몰래 승인하지 않는다.
- Remote Control session/mobile/browser/push는 실행하지 않았다.
- v1은 sender `sequence`를 보존하지만 reorder buffer/gap retransmission은 없다.
- SSH는 doctor diagnostic만 구현하며 v1 message transport는 direct TLS TCP 하나다.
- workspace의 `.git`은 metadata 없는 read-only placeholder라 Git repository initialization/commit은 수행할 수 없었다.

## 다음 명령

현재 service 상태:

```text
research-peer status
systemctl --user status research-peer.service
```

실제 pairing 전:

```text
research-peer doctor
research-peer init --listen THIS_BIND_HOST:THIS_HIGH_PORT
research-peer room create retrieval-toy
```

상대 결과를 받은 뒤:

```text
research-peer doctor --peer PEER_HOST:PEER_PORT --expect-fingerprint TLS_FINGERPRINT
```
